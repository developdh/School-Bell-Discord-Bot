"""
학교종 Discord 봇 — 클라이언트, 이벤트 핸들러, 스케줄러
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

import discord

from app.config import KST, PREFIX, TTS_CACHE, _BASE_DIR, _FFMPEG, log
from app.domain.commands import (
    AddBreakCommand, AttendanceCommand, BreakDeleteCommand,
    BreakEndCommand, BreakListCommand, ClosePanelCommand,
    HelpCommand, OpenPanelCommand, PersonalPauseCommand,
    PersonalResumeCommand, PresetDeleteCommand, PresetListCommand,
    PresetRunCommand, PresetSaveCommand, RecurringBreakAddCommand,
    RecurringBreakDeleteCommand, RecurringBreakListCommand,
    RefreshPanelCommand, SetRemainingCommand, SetTimerCommand,
    ShutdownAllCommand, StatusCommand, StatsCommand, StopTimerCommand,
    VoicePinCommand, VoiceUnpinCommand,
)
from app.domain.models import BreakEntry, GuildState, Timer
from app.parsers.command_parser import parse_command
from app.repositories.state_repository import load_state, save_state
from app.utils.time_utils import (
    fmt_dur,
    fmt_mm_ss,
    next_occurrence_ts,
    now_ts,
)

# ── Runtime state ─────────────────────────────────────────────────────────────
guild_states:  dict[int, GuildState]    = {}
guild_locks:   dict[int, asyncio.Lock]  = {}
guild_tasks:   dict[int, asyncio.Task]  = {}
voice_queues:  dict[int, asyncio.Queue] = {}
voice_workers: dict[int, asyncio.Task]  = {}
panel_tasks:   dict[int, asyncio.Task]  = {}


def get_guild_state(gid: int) -> GuildState:
    if gid not in guild_states:
        guild_states[gid] = GuildState()
        guild_locks[gid]  = asyncio.Lock()
        voice_queues[gid] = asyncio.Queue()
    return guild_states[gid]


def _save() -> None:
    """guild_states를 JSON에 저장하는 단축 호출."""
    save_state(guild_states)


# ── Timer ops ─────────────────────────────────────────────────────────────────

def timer_pause(timer: Timer) -> None:
    if timer.remaining_on_personal_pause is not None:
        return
    timer.remaining_on_pause = max(0.0, timer.phase_end_at - now_ts())


def timer_resume(timer: Timer) -> None:
    if timer.remaining_on_personal_pause is not None:
        return
    rem = timer.remaining_on_pause or 0.0
    timer.phase_end_at       = now_ts() + rem
    timer.remaining_on_pause = None


def timer_personal_pause(timer: Timer, gs: GuildState) -> None:
    if gs.pause_until is not None and timer.remaining_on_pause is not None:
        timer.remaining_on_personal_pause = timer.remaining_on_pause
        timer.remaining_on_pause = None
    else:
        timer.remaining_on_personal_pause = max(0.0, timer.phase_end_at - now_ts())


def timer_personal_resume(timer: Timer, gs: GuildState) -> None:
    rem = timer.remaining_on_personal_pause or 0.0
    timer.remaining_on_personal_pause = None
    if gs.pause_until is not None:
        timer.remaining_on_pause = rem
    else:
        timer.phase_end_at = now_ts() + rem


def _accumulate_stats(gs: GuildState, name: str, mode: str, seconds: float) -> None:
    if seconds <= 0:
        return
    date_key = datetime.now(KST).strftime("%Y-%m-%d")
    day = gs.stats.setdefault(date_key, {})
    entry = day.setdefault(name, {"study": 0.0, "rest": 0.0})
    entry["study" if mode == "study" else "rest"] += seconds


# ── TTS ───────────────────────────────────────────────────────────────────────

async def _make_tts(sentence: str, path: Path) -> bool:
    try:
        import edge_tts
        comm = edge_tts.Communicate(sentence, voice="ko-KR-SunHiNeural")
        await comm.save(str(path))
        if path.exists() and path.stat().st_size > 0:
            log.info("TTS 생성(edge-tts) → %s", path.name)
            return True
        log.warning("edge-tts 파일 크기 0, gTTS 시도")
    except Exception as e:
        log.warning("edge-tts 실패, gTTS 시도: %s", e)

    try:
        from gtts import gTTS
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: gTTS(text=sentence, lang="ko").save(str(path)),
        )
        if path.exists() and path.stat().st_size > 0:
            log.info("TTS 생성(gTTS) → %s", path.name)
            return True
        log.warning("gTTS 파일 크기 0")
    except Exception as e:
        log.warning("gTTS 실패: %s", e)

    return False


async def _get_tts_path_keyed(sentence: str, cache_key: str) -> Path | None:
    TTS_CACHE.mkdir(exist_ok=True)
    path = TTS_CACHE / cache_key
    if path.exists() and path.stat().st_size > 0:
        return path
    ok = await _make_tts(sentence, path)
    return path if ok else None


# ── Voice ─────────────────────────────────────────────────────────────────────

async def ensure_voice_connected(gid: int, gs: GuildState) -> discord.VoiceClient | None:
    vc_id = gs.last_voice_channel_id
    if not vc_id:
        return None

    vc_channel = bot.get_channel(vc_id)
    if not isinstance(vc_channel, discord.VoiceChannel):
        log.warning("음성채널 채널 객체 없음 gid=%d cid=%d", gid, vc_id)
        return None

    existing = discord.utils.get(bot.voice_clients, guild=vc_channel.guild)
    if existing and existing.is_connected():
        if existing.channel.id != vc_id:
            try:
                await existing.move_to(vc_channel)
                log.info("음성채널 이동 guild=%d → ch=%d", gid, vc_id)
            except Exception:
                log.exception("음성채널 이동 실패 guild=%d", gid)
        return existing  # type: ignore[return-value]

    try:
        vc = await vc_channel.connect(timeout=10.0)
        log.info("음성채널 연결 guild=%d ch=%d", gid, vc_id)
        return vc
    except discord.ClientException:
        return discord.utils.get(bot.voice_clients, guild=vc_channel.guild)  # type: ignore[return-value]
    except Exception:
        log.exception("음성채널 연결 실패 guild=%d", gid)
        return None


async def ensure_voice_disconnected(gid: int) -> None:
    guild = bot.get_guild(gid)
    if guild is None:
        return
    existing = discord.utils.get(bot.voice_clients, guild=guild)
    if existing:
        try:
            await existing.disconnect(force=True)
            log.info("음성채널 해제 guild=%d", gid)
        except Exception:
            log.exception("음성채널 해제 실패 guild=%d", gid)


async def _play_voice_audio(vc: discord.VoiceClient, path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        log.warning("재생 파일 없거나 크기 0: %s", path)
        return

    if vc.is_playing():
        log.warning("이미 재생 중 — stop 후 재생: %s", path.name)
        vc.stop()
        await asyncio.sleep(0.1)

    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def after(err: Exception | None) -> None:
        if not done.done():
            if err:
                loop.call_soon_threadsafe(done.set_exception, err)
            else:
                loop.call_soon_threadsafe(done.set_result, None)

    try:
        vc.play(discord.FFmpegOpusAudio(str(path), executable=_FFMPEG), after=after)
        log.debug("재생 시작: %s", path.name)
    except Exception as exc:
        log.warning("FFmpegOpusAudio 오류 [%s]: %s  (%s)", path.name, exc, type(exc).__name__)
        return

    try:
        await asyncio.wait_for(asyncio.shield(done), timeout=300.0)
        log.debug("재생 완료: %s", path.name)
    except asyncio.TimeoutError:
        log.warning("재생 타임아웃 [%s]", path.name)
        try:
            vc.stop()
        except Exception:
            pass
    except Exception as exc:
        log.warning("재생 오류 [%s]: %s", path.name, exc)


async def _voice_worker(gid: int) -> None:
    log.info("음성 워커 시작 guild=%d", gid)
    q = voice_queues[gid]
    try:
        while True:
            sentence, cache_key = await q.get()
            try:
                gs = guild_states.get(gid)
                if gs is None or not gs.state_exists():
                    log.debug("상태 없음, 오디오 스킵 guild=%d", gid)
                    continue

                tts_path = await _get_tts_path_keyed(sentence, cache_key)

                vc = await ensure_voice_connected(gid, gs)
                if vc is None:
                    log.debug("음성 연결 불가, 오디오 스킵 guild=%d", gid)
                    continue

                for bell in (_BASE_DIR / "bell.mp3", _BASE_DIR / "bell.wav"):
                    if bell.exists():
                        await _play_voice_audio(vc, bell)
                        break
                else:
                    log.debug("bell.mp3 / bell.wav 없음, 벨 스킵")

                if tts_path:
                    await _play_voice_audio(vc, tts_path)
                else:
                    log.debug("TTS 생성 실패, TTS 스킵 guild=%d", gid)

            except Exception:
                log.exception("오디오 재생 오류 guild=%d", gid)
            finally:
                q.task_done()

    except asyncio.CancelledError:
        log.info("음성 워커 종료 guild=%d", gid)


def _ensure_voice_worker(gid: int) -> None:
    w = voice_workers.get(gid)
    if w is None or w.done():
        if gid not in voice_queues:
            voice_queues[gid] = asyncio.Queue()
        voice_workers[gid] = asyncio.create_task(_voice_worker(gid))


def _cancel_voice_worker(gid: int) -> None:
    w = voice_workers.pop(gid, None)
    if w and not w.done():
        w.cancel()


def play_event_audio(gid: int, sentence: str, cache_key: str) -> None:
    q = voice_queues.get(gid)
    if q is None:
        return
    q.put_nowait((sentence, cache_key))
    _ensure_voice_worker(gid)
    log.debug("오디오 큐 추가 guild=%d [%s]", gid, cache_key)


# ── Notifications ─────────────────────────────────────────────────────────────

async def _get_channel(cid: int) -> discord.TextChannel | None:
    ch = bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception:
            pass
    return ch  # type: ignore[return-value]


async def _break_channels(gs: GuildState) -> list[discord.TextChannel]:
    seen: set[int] = set()
    result: list[discord.TextChannel] = []
    ids = {t.channel_id for t in gs.timers.values()}
    if gs.last_channel_id:
        ids.add(gs.last_channel_id)
    for cid in ids:
        if cid not in seen:
            ch = await _get_channel(cid)
            if ch:
                result.append(ch)
                seen.add(cid)
    return result


async def notify_transition(gid: int, cid: int, name: str, mode: str) -> None:
    ch = await _get_channel(cid)
    if ch:
        label = "휴식" if mode == "rest" else "공부"
        await ch.send(f"🔔 학교종! **{name}** {label}")

    label_kr  = "공부" if mode == "study" else "휴식"
    sentence  = f"{name} {label_kr} 시작."
    safe_name = re.sub(r"[^\w가-힣]", "_", name)[:20]
    cache_key = f"{gid}_tr_{safe_name}_{mode}.mp3"
    play_event_audio(gid, sentence, cache_key)


async def notify_break_event(
    gid: int, gs: GuildState, brk: BreakEntry, end_ts: float, extending: bool
) -> None:
    end_dt = datetime.fromtimestamp(end_ts, tz=KST)
    if extending:
        msg = (
            f"⏸️ **{brk.label}** — 일시정지 연장 "
            f"→ {end_dt.strftime('%H:%M:%S')}까지"
        )
    else:
        msg = (
            f"⏸️ **{brk.label}** 쉬는시간! "
            f"{fmt_dur(brk.duration_sec)} 일시정지 "
            f"(→ {end_dt.strftime('%H:%M:%S')} 재개)"
        )
    for ch in await _break_channels(gs):
        await ch.send(msg)
    if not extending:
        sentence   = f"{brk.label} 시작."
        safe_label = re.sub(r"[^\w가-힣]", "_", brk.label)[:20]
        cache_key  = f"{gid}_brk_{safe_label}.mp3"
        play_event_audio(gid, sentence, cache_key)


async def notify_resume(gid: int, gs: GuildState) -> None:
    for ch in await _break_channels(gs):
        await ch.send("▶️ 쉬는시간 종료! 모든 타이머 재개")
    play_event_audio(gid, "쉬는시간 종료.", f"{gid}_resume.mp3")


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def guild_scheduler(gid: int) -> None:
    log.info("스케줄러 시작 guild=%d", gid)
    try:
        while True:
            await asyncio.sleep(0.5)
            lock = guild_locks.get(gid)
            if lock is None:
                continue
            async with lock:
                gs = guild_states.get(gid)
                if gs is None:
                    return
                ts = now_ts()

                # 1) 쉬는시간 체크 (일반 + 정규)
                for brk in gs.breaks + gs.recurring_breaks:
                    bt = brk.next_ts
                    if bt == 0.0 or ts < bt:
                        continue
                    end_ts   = ts + brk.duration_sec
                    already  = gs.pause_until is not None
                    if not already or gs.pause_until < end_ts:
                        if not already:
                            for _n, _t in gs.timers.items():
                                if _t.remaining_on_personal_pause is None:
                                    if _t.last_accounted_at > 0 and _t.last_accounted_at < ts:
                                        _accumulate_stats(gs, _n, _t.mode, ts - _t.last_accounted_at)
                                    _t.last_accounted_at = ts
                            for t in gs.timers.values():
                                timer_pause(t)
                        gs.pause_until = end_ts
                        await notify_break_event(gid, gs, brk, end_ts, already)
                        asyncio.create_task(update_status_panel(gid))
                    brk.next_ts = next_occurrence_ts(brk.hhmm)

                # 2) 일시정지 종료 체크
                if gs.pause_until is not None and ts >= gs.pause_until:
                    gs.pause_until = None
                    for t in gs.timers.values():
                        timer_resume(t)
                        t.last_accounted_at = ts
                    await notify_resume(gid, gs)
                    asyncio.create_task(update_status_panel(gid))

                # 3) 개인 타이머 전환 체크 (pause 중 아닐 때만)
                if gs.pause_until is None:
                    for name, t in list(gs.timers.items()):
                        if t.remaining_on_personal_pause is not None:
                            continue

                        # Stats accumulation
                        if t.last_accounted_at > 0 and t.last_accounted_at < ts:
                            _accumulate_stats(gs, name, t.mode, ts - t.last_accounted_at)
                        t.last_accounted_at = ts

                        # Auto-stop: 시간 제한
                        if t.auto_stop_ts is not None and ts >= t.auto_stop_ts:
                            cid_as = t.channel_id
                            del gs.timers[name]
                            _save()
                            ch = await _get_channel(cid_as)
                            if ch:
                                await ch.send(f"🏁 **{name}** 시간 도달 → 자동 종료")
                            safe = re.sub(r"[^\w가-힣]", "_", name)[:20]
                            play_event_audio(gid, f"{name} 자동 종료.", f"{gid}_as_{safe}.mp3")
                            asyncio.create_task(update_status_panel(gid))
                            if not gs.state_exists():
                                _cancel_voice_worker(gid)
                                asyncio.create_task(ensure_voice_disconnected(gid))
                            continue

                        if ts >= t.phase_end_at:
                            new_mode = "rest" if t.mode == "study" else "study"

                            # Auto-stop: 반복 횟수
                            if new_mode == "study" and t.auto_stop_cycles is not None:
                                t.cycle_count += 1
                                if t.cycle_count >= t.auto_stop_cycles:
                                    cycles = t.auto_stop_cycles
                                    cid_as = t.channel_id
                                    del gs.timers[name]
                                    _save()
                                    ch = await _get_channel(cid_as)
                                    if ch:
                                        await ch.send(
                                            f"🏁 **{name}** "
                                            f"{cycles}회 반복 완료 → 자동 종료"
                                        )
                                    safe = re.sub(r"[^\w가-힣]", "_", name)[:20]
                                    play_event_audio(gid, f"{name} 자동 종료.", f"{gid}_as_{safe}.mp3")
                                    asyncio.create_task(update_status_panel(gid))
                                    if not gs.state_exists():
                                        _cancel_voice_worker(gid)
                                        asyncio.create_task(ensure_voice_disconnected(gid))
                                    continue

                            overshoot = ts - t.phase_end_at
                            t.mode         = new_mode
                            t.phase_end_at = ts + getattr(t, f"{new_mode}_sec") - overshoot
                            await notify_transition(gid, t.channel_id, name, new_mode)
                            asyncio.create_task(update_status_panel(gid))

                # 3-1) Stats periodic save (~30초마다)
                if gs.timers and ts - gs.last_stats_save >= 30:
                    gs.last_stats_save = ts
                    _save()

                # 4) 음성채널 연결 유지
                if gs.state_exists() and gs.last_voice_channel_id:
                    _ensure_voice_worker(gid)
                    q = voice_queues.get(gid)
                    if q is not None and q.empty():
                        asyncio.create_task(ensure_voice_connected(gid, gs))
                elif not gs.state_exists():
                    _cancel_voice_worker(gid)
                    asyncio.create_task(ensure_voice_disconnected(gid))

    except asyncio.CancelledError:
        log.info("스케줄러 종료 guild=%d", gid)
        _cancel_voice_worker(gid)
        await ensure_voice_disconnected(gid)
    except Exception:
        log.exception("스케줄러 예외 guild=%d", gid)


def ensure_scheduler(gid: int) -> None:
    t = guild_tasks.get(gid)
    if t is None or t.done():
        guild_tasks[gid] = asyncio.create_task(guild_scheduler(gid))


# ── Status builder ────────────────────────────────────────────────────────────

def build_status(gs: GuildState) -> str:
    ts = now_ts()
    lines: list[str] = []

    # 일시정지 배너
    if gs.pause_until is not None:
        rem = gs.pause_until - ts
        edt = datetime.fromtimestamp(gs.pause_until, tz=KST)
        lines.append(
            f"⏸️ **일시정지 중** — {edt.strftime('%H:%M:%S')} 재개 "
            f"(남은 시간 : {fmt_mm_ss(rem)})"
        )

    # 개인 타이머
    if gs.timers:
        lines.append("**📋 개인 타이머**")
        for name, t in gs.timers.items():
            ml = "공부" if t.mode == "study" else "휴식"
            if t.remaining_on_personal_pause is not None:
                rem   = t.remaining_on_personal_pause
                end_s = "(개인 일시정지)"
            elif gs.pause_until is not None and t.remaining_on_pause is not None:
                rem   = t.remaining_on_pause
                end_s = "(일시정지)"
            else:
                rem   = t.phase_end_at - ts
                end_s = datetime.fromtimestamp(t.phase_end_at, tz=KST).strftime("%H:%M:%S")
            auto_info = ""
            if t.auto_stop_cycles is not None:
                auto_info += f" [{t.cycle_count}/{t.auto_stop_cycles}회]"
            if t.auto_stop_ts is not None:
                _edt = datetime.fromtimestamp(t.auto_stop_ts, tz=KST)
                auto_info += f" [끝 {_edt.strftime('%H:%M')}]"
            lines.append(f"  • **{name}** [{ml}] 남은 시간 : {fmt_mm_ss(rem)} → {end_s}{auto_info}")
    else:
        lines.append("📋 등록된 타이머 없음")

    # 쉬는시간 목록
    if gs.breaks:
        lines.append("**🔔 쉬는시간 목록**")
        for b in gs.breaks:
            nts = b.next_ts or next_occurrence_ts(b.hhmm)
            ndt = datetime.fromtimestamp(nts, tz=KST)
            lines.append(
                f"  • **{b.label}** → {ndt.strftime('%m/%d %H:%M')} "
                f"({fmt_dur(b.duration_sec)})"
            )
    else:
        lines.append("🔔 등록된 쉬는시간 없음")

    # 정규쉬는시간 목록
    if gs.recurring_breaks:
        lines.append("**🔁 정규쉬는시간 목록**")
        for b in gs.recurring_breaks:
            nts = b.next_ts or next_occurrence_ts(b.hhmm)
            ndt = datetime.fromtimestamp(nts, tz=KST)
            lines.append(
                f"  • **{b.label}** 매일 {b.hhmm} "
                f"({fmt_dur(b.duration_sec)}) "
                f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
            )

    # 음성채널 고정 표시
    if gs.pinned_voice_channel_id:
        lines.append(f"🔊 음성채널 고정: <#{gs.pinned_voice_channel_id}>")

    return "\n".join(lines)


# ── Stats / Attendance builders ───────────────────────────────────────────────

def build_stats(gs: GuildState, name: str | None = None) -> str:
    stats = gs.stats
    if name:
        lines = [f"📊 **{name} 통계**"]
        today = datetime.now(KST).date()
        total_study = 0.0
        found = False
        from datetime import timedelta
        for d in range(7):
            dt = today - timedelta(days=d)
            dk = dt.strftime("%Y-%m-%d")
            day = stats.get(dk, {})
            entry = day.get(name)
            if entry:
                found = True
                s = entry.get("study", 0)
                r = entry.get("rest", 0)
                total_study += s
                label = "오늘" if d == 0 else dt.strftime("%m/%d")
                lines.append(f"  • {label} — 공부 {fmt_dur(int(s))} / 휴식 {fmt_dur(int(r))}")
        if not found:
            lines.append("  기록 없음")
        else:
            lines.append(f"  **총 공부: {fmt_dur(int(total_study))}**")
        return "\n".join(lines)
    else:
        today_key = datetime.now(KST).strftime("%Y-%m-%d")
        day = stats.get(today_key, {})
        if not day:
            return f"📊 **오늘의 통계** ({today_key})\n  기록 없음"
        lines = [f"📊 **오늘의 통계** ({today_key})"]
        for uname, entry in sorted(day.items(), key=lambda x: x[1].get("study", 0), reverse=True):
            s = entry.get("study", 0)
            r = entry.get("rest", 0)
            lines.append(f"  • **{uname}** 공부 {fmt_dur(int(s))} / 휴식 {fmt_dur(int(r))}")
        return "\n".join(lines)


def build_attendance(gs: GuildState) -> str:
    today_key = datetime.now(KST).strftime("%Y-%m-%d")
    day = gs.stats.get(today_key, {})
    lines = [f"📋 **출석부** ({today_key})"]
    if not day:
        lines.append("  기록 없음")
    else:
        for uname, entry in sorted(day.items(), key=lambda x: x[1].get("study", 0), reverse=True):
            study_sec = entry.get("study", 0)
            check = "✅" if study_sec >= 3600 else "❌"
            lines.append(f"  {check} **{uname}** — {fmt_dur(int(study_sec))}")
    lines.append("  (기준: 공부 60분 이상)")
    return "\n".join(lines)


# ── Status Panel ──────────────────────────────────────────────────────────────

def build_status_embed(gs: GuildState, gid: int) -> discord.Embed:
    ts = now_ts()
    now_dt = datetime.now(KST)

    if gs.pause_until is not None:
        color = 0xFFA500
    elif gs.timers:
        color = 0x2ECC71
    else:
        color = 0x95A5A6

    embed = discord.Embed(title="🏫 학교종 상태 패널", color=color)

    # 개요
    overview_parts: list[str] = []
    active = sum(1 for t in gs.timers.values() if t.remaining_on_personal_pause is None)
    paused = len(gs.timers) - active
    overview_parts.append(f"타이머: {len(gs.timers)}명 (활성 {active} / 정지 {paused})")
    if gs.pause_until is not None:
        rem = gs.pause_until - ts
        edt = datetime.fromtimestamp(gs.pause_until, tz=KST)
        overview_parts.append(
            f"⏸️ 일시정지 중 — {edt.strftime('%H:%M:%S')} 재개 "
            f"(남은 {fmt_mm_ss(rem)})"
        )
    embed.add_field(name="📊 개요", value="\n".join(overview_parts), inline=False)

    # 타이머
    if gs.timers:
        timer_lines: list[str] = []
        for name, t in gs.timers.items():
            ml = "공부" if t.mode == "study" else "휴식"
            icon = "📗" if t.mode == "study" else "📙"
            if t.remaining_on_personal_pause is not None:
                rem = t.remaining_on_personal_pause
                status = "⏸️정지"
            elif gs.pause_until is not None and t.remaining_on_pause is not None:
                rem = t.remaining_on_pause
                status = "⏸️쉬는시간"
            else:
                rem = t.phase_end_at - ts
                status = f"→{datetime.fromtimestamp(t.phase_end_at, tz=KST).strftime('%H:%M:%S')}"
            auto_info = ""
            if t.auto_stop_cycles is not None:
                auto_info += f" [{t.cycle_count}/{t.auto_stop_cycles}회]"
            if t.auto_stop_ts is not None:
                _edt = datetime.fromtimestamp(t.auto_stop_ts, tz=KST)
                auto_info += f" [끝{_edt.strftime('%H:%M')}]"
            timer_lines.append(
                f"{icon} **{name}** [{ml}] {fmt_mm_ss(rem)} {status}{auto_info}"
            )
        embed.add_field(
            name=f"📋 타이머 ({len(gs.timers)})",
            value="\n".join(timer_lines),
            inline=False,
        )

    # 쉬는시간
    all_breaks = gs.breaks + gs.recurring_breaks
    if all_breaks:
        brk_lines: list[str] = []
        for b in gs.breaks:
            nts = b.next_ts or next_occurrence_ts(b.hhmm)
            ndt = datetime.fromtimestamp(nts, tz=KST)
            brk_lines.append(
                f"🔔 **{b.label}** {ndt.strftime('%m/%d %H:%M')} "
                f"({fmt_dur(b.duration_sec)})"
            )
        for b in gs.recurring_breaks:
            nts = b.next_ts or next_occurrence_ts(b.hhmm)
            ndt = datetime.fromtimestamp(nts, tz=KST)
            brk_lines.append(
                f"🔁 **{b.label}** 매일 {b.hhmm} "
                f"({fmt_dur(b.duration_sec)}) "
                f"→ {ndt.strftime('%m/%d %H:%M')}"
            )
        embed.add_field(
            name=f"⏰ 쉬는시간 ({len(all_breaks)})",
            value="\n".join(brk_lines),
            inline=False,
        )

    # 오늘 통계 / 출석
    today_key = now_dt.strftime("%Y-%m-%d")
    day = gs.stats.get(today_key, {})
    if day:
        stat_lines: list[str] = []
        for uname, entry in sorted(day.items(), key=lambda x: x[1].get("study", 0), reverse=True):
            s = entry.get("study", 0)
            r = entry.get("rest", 0)
            check = "✅" if s >= 3600 else "❌"
            stat_lines.append(
                f"{check} **{uname}** 공부 {fmt_dur(int(s))} / 휴식 {fmt_dur(int(r))}"
            )
        embed.add_field(
            name=f"📈 오늘 통계·출석 ({today_key})",
            value="\n".join(stat_lines),
            inline=False,
        )

    embed.set_footer(
        text=f"마지막 갱신: {now_dt.strftime('%H:%M:%S')} KST  |  10초마다 자동 갱신"
    )
    return embed


async def fetch_status_panel_message(
    gid: int, gs: GuildState
) -> discord.Message | None:
    ch_id = gs.status_panel_channel_id
    msg_id = gs.status_panel_message_id
    if not ch_id or not msg_id:
        return None
    ch = bot.get_channel(ch_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(ch_id)
        except Exception:
            gs.status_panel_channel_id = None
            gs.status_panel_message_id = None
            _save()
            return None
    try:
        return await ch.fetch_message(msg_id)  # type: ignore[union-attr]
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        gs.status_panel_channel_id = None
        gs.status_panel_message_id = None
        _save()
        return None


async def update_status_panel(gid: int) -> None:
    gs = guild_states.get(gid)
    if gs is None:
        return
    panel_msg = await fetch_status_panel_message(gid, gs)
    if panel_msg is None:
        return
    try:
        embed = build_status_embed(gs, gid)
        await panel_msg.edit(embed=embed)
    except Exception:
        log.exception("패널 갱신 실패 guild=%d", gid)


async def panel_updater_loop(gid: int) -> None:
    log.info("패널 갱신 루프 시작 guild=%d", gid)
    try:
        while True:
            await asyncio.sleep(10)
            gs = guild_states.get(gid)
            if gs is None or not gs.status_panel_message_id:
                break
            await update_status_panel(gid)
    except asyncio.CancelledError:
        log.info("패널 갱신 루프 종료 guild=%d", gid)


def ensure_panel_task(gid: int) -> None:
    t = panel_tasks.get(gid)
    if t is None or t.done():
        panel_tasks[gid] = asyncio.create_task(panel_updater_loop(gid))


def cancel_panel_task(gid: int) -> None:
    t = panel_tasks.pop(gid, None)
    if t and not t.done():
        t.cancel()


# ── Help builder ──────────────────────────────────────────────────────────────

def build_help() -> str:
    return (
        "📖 **학교종 봇 도움말**\n"
        "\n"
        "**1) 개인 타이머 설정/재설정**\n"
        "```\n"
        "--학교종 이름 10분공부 5분휴식\n"
        "--학교종 이름 30초공부 10초휴식\n"
        "--학교종 이름 1시간공부 10분휴식\n"
        "--학교종 김동희 10분공부 5분휴식 서채영 1시간공부 20분휴식\n"
        "```\n"
        "• 시간 단위: 초 / 분 / 시간 모두 가능합니다.\n"
        "• 공부/휴식 순서는 무관합니다.\n"
        "• 이미 등록된 이름이면 타이머가 재설정됩니다.\n"
        "\n"
        "**1-1) 자동 종료 조건** (선택)\n"
        "```\n"
        "--학교종 김동희 10분공부 5분휴식 4회반복\n"
        "--학교종 김동희 10분공부 5분휴식 오늘끝 18:00\n"
        "--학교종 김동희 10분공부 5분휴식 4회반복 오늘끝 18:00\n"
        "```\n"
        "• N회반복: 공부→휴식을 N번 반복 후 자동 종료\n"
        "• 오늘끝 HH:MM: 해당 시각에 자동 종료\n"
        "• 재시작 시 진행도는 리셋됩니다.\n"
        "\n"
        "**2) 개인 타이머 종료**\n"
        "```\n"
        "--학교종 이름 종료\n"
        "--학교종 김동희 종료\n"
        "```\n"
        "\n"
        "**3) 개인 일시정지 / 재개**\n"
        "```\n"
        "--학교종 일시정지 김동희\n"
        "--학교종 재개 김동희\n"
        "```\n"
        "• 전체 쉬는시간과 별개로 개인 타이머만 정지/재개합니다.\n"
        "• 전체 쉬는시간 중에도 개인 일시정지 상태는 유지됩니다.\n"
        "\n"
        "**4) 남은시간 수정**\n"
        "```\n"
        "--학교종 남은시간 김동희 10분\n"
        "--학교종 남은시간 김동희 30초\n"
        "```\n"
        "• 현재 페이즈(공부/휴식)의 남은 시간을 변경합니다.\n"
        "\n"
        "**5) 전체 종료** (모든 타이머/쉬는시간 삭제 + 스케줄러 중지)\n"
        "```\n"
        "--학교종 종료\n"
        "```\n"
        "\n"
        "**6) 쉬는시간 등록**\n"
        "```\n"
        "--학교종 쉬는시간 점심시간 18:00 20분\n"
        "--학교종 쉬는시간 쉬는시간 14:30 10초\n"
        "--학교종 쉬는시간 점심시간 12:00 1시간\n"
        "```\n"
        "• HH:MM이 이미 지났으면 다음 날로 자동 예약됩니다.\n"
        "\n"
        "**7) 쉬는시간 목록 / 삭제**\n"
        "```\n"
        "--학교종 쉬는시간 목록\n"
        "--학교종 쉬는시간 삭제 점심시간\n"
        "```\n"
        "\n"
        "**8) 정규쉬는시간** (매일 반복)\n"
        "```\n"
        "--학교종 정규쉬는시간 추가 점심 12:00 1시간\n"
        "--학교종 정규쉬는시간 목록\n"
        "--학교종 정규쉬는시간 삭제 점심\n"
        "```\n"
        "• 매일 같은 시각에 자동 발동하는 쉬는시간입니다.\n"
        "• 봇이 꺼져 있던 동안 지나간 시각은 소급 적용되지 않습니다.\n"
        "\n"
        "**9) 쉬는시간 강제 종료** (현재 일시정지 즉시 해제, 스케줄은 유지)\n"
        "```\n"
        "--학교종 쉬는시간 끝\n"
        "```\n"
        "\n"
        "**10) 음성채널 고정 / 해제**\n"
        "```\n"
        "--학교종 음성채널 고정\n"
        "--학교종 음성채널 해제\n"
        "```\n"
        "• 고정하면 봇 재시작 후에도 해당 채널에 자동 접속합니다.\n"
        "• 해제하면 명령 시점의 사용자 음성채널을 따릅니다.\n"
        "\n"
        "**11) 프리셋 저장 / 실행 / 목록 / 삭제**\n"
        "```\n"
        "--학교종 프리셋 저장 집중모드 김동희 10분공부 5분휴식 4회반복\n"
        "--학교종 프리셋 실행 집중모드\n"
        "--학교종 프리셋 목록\n"
        "--학교종 프리셋 삭제 집중모드\n"
        "```\n"
        "• 자주 쓰는 명령을 이름으로 저장해두고 한 번에 실행합니다.\n"
        "\n"
        "**12) 통계**\n"
        "```\n"
        "--학교종 통계\n"
        "--학교종 통계 김동희\n"
        "```\n"
        "• 전체: 오늘의 공부/휴식 시간 요약\n"
        "• 개인: 최근 7일간 기록\n"
        "• 봇이 켜져 있던 동안 실제 관측 시간만 집계합니다.\n"
        "\n"
        "**13) 출석**\n"
        "```\n"
        "--학교종 출석\n"
        "```\n"
        "• 오늘 공부 60분 이상이면 출석 ✅\n"
        "\n"
        "**14) 상태 패널** (Discord Embed, 자동 갱신)\n"
        "```\n"
        "--학교종 패널\n"
        "--학교종 패널 해제\n"
        "--학교종 패널 새로고침\n"
        "```\n"
        "• 패널: Embed 메시지를 생성하고 10초마다 자동 갱신합니다.\n"
        "• 해제: 자동 갱신을 중지합니다.\n"
        "• 새로고침: 즉시 패널을 갱신합니다.\n"
        "\n"
        "**15) 상태 출력**\n"
        "```\n"
        "--학교종 상태\n"
        "```\n"
        "\n"
        "**16) 도움말**\n"
        "```\n"
        "--학교종 도움말\n"
        "--학교종 help\n"
        "```\n"
        "\n"
        "🔊 **음성 안내**\n"
        "• 명령을 보낸 사용자가 음성채널에 있으면 봇이 그 채널에 상주하며,\n"
        "  타이머 전환·쉬는시간마다 종소리(bell.mp3) + TTS로 안내합니다.\n"
        "• `--학교종 음성채널 고정` 으로 채널을 영구 지정할 수 있습니다.\n"
        "• 필요 권한: `Connect` / `Speak`\n"
        "• TTS 패키지: `pip install edge-tts` (또는 gTTS fallback)\n"
        "• FFmpeg 필수: `brew install ffmpeg` / `sudo apt install ffmpeg`"
    )


# ── Message splitter ──────────────────────────────────────────────────────────

async def send_split(ch: discord.abc.Messageable, text: str, limit: int = 1900) -> None:
    if len(text) <= limit:
        await ch.send(text)
        return
    chunk = ""
    for line in text.split("\n"):
        add = ("\n" + line) if chunk else line
        if len(chunk) + len(add) > limit:
            if chunk:
                await ch.send(chunk)
            chunk = line
        else:
            chunk += add
    if chunk:
        await ch.send(chunk)


# ── Bot ───────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states    = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready() -> None:
    assert bot.user
    log.info("로그인: %s (id=%d)", bot.user, bot.user.id)
    ts = now_ts()
    for gid_str, data in load_state().items():
        gid = int(gid_str)
        gs  = get_guild_state(gid)
        gs.last_channel_id = data.get("last_channel_id")
        gs.pinned_voice_channel_id = data.get("pinned_voice_channel_id")
        if gs.pinned_voice_channel_id:
            gs.last_voice_channel_id = gs.pinned_voice_channel_id
        gs.presets = data.get("presets", {})
        gs.stats = data.get("stats", {})
        gs.status_panel_channel_id = data.get("status_panel_channel_id")
        gs.status_panel_message_id = data.get("status_panel_message_id")

        # 쉬는시간 복구
        for b in data.get("breaks", []):
            gs.breaks.append(BreakEntry.from_saved(b))

        # 정규쉬는시간 복구
        for b in data.get("recurring_breaks", []):
            gs.recurring_breaks.append(BreakEntry.from_saved(b))

        # 타이머 복구
        for name, td in data.get("timers", {}).items():
            gs.timers[name] = Timer.from_saved(td, ts)

        if gs.timers or gs.breaks or gs.recurring_breaks:
            ensure_scheduler(gid)

        # 패널 복구
        if gs.status_panel_message_id:
            ensure_panel_task(gid)

    log.info("준비 완료")


@bot.event
async def on_message(msg: discord.Message) -> None:
    if msg.author.bot:
        return
    if not msg.content.startswith(PREFIX):
        return

    raw = msg.content[len(PREFIX):].strip()
    if not raw:
        return

    gid = msg.guild.id if msg.guild else msg.author.id
    cid = msg.channel.id

    gs   = get_guild_state(gid)
    lock = guild_locks[gid]

    # 음성채널 추적
    if (
        not gs.pinned_voice_channel_id
        and msg.guild
        and hasattr(msg.author, "voice")
        and msg.author.voice
        and msg.author.voice.channel
    ):
        gs.last_voice_channel_id = msg.author.voice.channel.id

    actions = parse_command(raw)
    if not actions:
        await msg.channel.send("❌ 명령어를 인식할 수 없습니다.")
        return

    async with lock:
        gs.last_channel_id = cid
        replies: list[str] = []

        for cmd in actions:

            # ── 도움말 ──
            if isinstance(cmd, HelpCommand):
                replies.append(build_help())

            # ── 상태 ──
            elif isinstance(cmd, StatusCommand):
                replies.append(build_status(gs))

            # ── 통계 ──
            elif isinstance(cmd, StatsCommand):
                replies.append(build_stats(gs, cmd.name))

            # ── 출석 ──
            elif isinstance(cmd, AttendanceCommand):
                replies.append(build_attendance(gs))

            # ── 패널 ──
            elif isinstance(cmd, OpenPanelCommand):
                embed = build_status_embed(gs, gid)
                panel_msg = await msg.channel.send(embed=embed)
                gs.status_panel_channel_id = msg.channel.id
                gs.status_panel_message_id = panel_msg.id
                _save()
                ensure_panel_task(gid)
                replies.append("✅ 상태 패널 생성 (10초마다 자동 갱신)")

            # ── 패널 해제 ──
            elif isinstance(cmd, ClosePanelCommand):
                if gs.status_panel_message_id:
                    cancel_panel_task(gid)
                    gs.status_panel_channel_id = None
                    gs.status_panel_message_id = None
                    _save()
                    replies.append("✅ 상태 패널 자동 갱신 해제")
                else:
                    replies.append("ℹ️ 활성화된 패널이 없습니다.")

            # ── 패널 새로고침 ──
            elif isinstance(cmd, RefreshPanelCommand):
                if gs.status_panel_message_id:
                    await update_status_panel(gid)
                    replies.append("✅ 패널 새로고침 완료")
                else:
                    replies.append("ℹ️ 활성화된 패널이 없습니다.")

            # ── 전체 종료 ──
            elif isinstance(cmd, ShutdownAllCommand):
                _ts = now_ts()
                if gs.pause_until is None:
                    for _n, _t in gs.timers.items():
                        if _t.remaining_on_personal_pause is None:
                            if _t.last_accounted_at > 0 and _t.last_accounted_at < _ts:
                                _accumulate_stats(gs, _n, _t.mode, _ts - _t.last_accounted_at)
                gs.timers.clear()
                gs.breaks.clear()
                gs.recurring_breaks.clear()
                gs.pause_until              = None
                gs.pinned_voice_channel_id  = None
                gs.last_voice_channel_id    = None
                gs.voice_notice_sent        = False
                _save()
                task = guild_tasks.pop(gid, None)
                if task:
                    task.cancel()
                _cancel_voice_worker(gid)
                asyncio.create_task(ensure_voice_disconnected(gid))
                asyncio.create_task(update_status_panel(gid))
                replies.append("✅ 전체 종료: 모든 타이머/쉬는시간 중지")

            # ── 쉬는시간 강제 종료 ──
            elif isinstance(cmd, BreakEndCommand):
                if gs.pause_until is None:
                    replies.append("ℹ️ 현재 일시정지 중이 아닙니다")
                else:
                    gs.pause_until = None
                    for t in gs.timers.values():
                        timer_resume(t)
                    await notify_resume(gid, gs)
                    asyncio.create_task(update_status_panel(gid))

            # ── 음성채널 고정 ──
            elif isinstance(cmd, VoicePinCommand):
                if (
                    msg.guild
                    and hasattr(msg.author, "voice")
                    and msg.author.voice
                    and msg.author.voice.channel
                ):
                    vc_ch = msg.author.voice.channel
                    gs.pinned_voice_channel_id = vc_ch.id
                    gs.last_voice_channel_id   = vc_ch.id
                    _save()
                    replies.append(f"✅ 음성채널 **{vc_ch.name}** 고정")
                else:
                    replies.append("❌ 음성채널에 먼저 접속해주세요.")

            # ── 음성채널 해제 ──
            elif isinstance(cmd, VoiceUnpinCommand):
                if gs.pinned_voice_channel_id:
                    gs.pinned_voice_channel_id = None
                    gs.last_voice_channel_id   = None
                    _save()
                    _cancel_voice_worker(gid)
                    asyncio.create_task(ensure_voice_disconnected(gid))
                    replies.append("✅ 음성채널 고정 해제")
                else:
                    replies.append("ℹ️ 고정된 음성채널이 없습니다.")

            # ── 종료 ──
            elif isinstance(cmd, StopTimerCommand):
                name = cmd.name
                if name in gs.timers:
                    _st = gs.timers[name]
                    if gs.pause_until is None and _st.remaining_on_personal_pause is None:
                        if _st.last_accounted_at > 0:
                            _accumulate_stats(gs, name, _st.mode, now_ts() - _st.last_accounted_at)
                    del gs.timers[name]
                    _save()
                    replies.append(f"✅ **{name}** 타이머 종료")
                    asyncio.create_task(update_status_panel(gid))
                    if not gs.state_exists():
                        _cancel_voice_worker(gid)
                        asyncio.create_task(ensure_voice_disconnected(gid))
                else:
                    replies.append(f"❌ **{name}** 타이머 없음")

            # ── 쉬는시간 등록 ──
            elif isinstance(cmd, AddBreakCommand):
                brk = BreakEntry(
                    label=cmd.label,
                    hhmm=cmd.hhmm,
                    duration_sec=cmd.duration_sec,
                    next_ts=next_occurrence_ts(cmd.hhmm),
                )
                gs.breaks.append(brk)
                _save()
                ndt = datetime.fromtimestamp(brk.next_ts, tz=KST)
                replies.append(
                    f"✅ 쉬는시간 **{cmd.label}** 등록 "
                    f"— {ndt.strftime('%m/%d %H:%M')} ({fmt_dur(cmd.duration_sec)})"
                )
                ensure_scheduler(gid)
                asyncio.create_task(update_status_panel(gid))

            # ── 쉬는시간 목록 ──
            elif isinstance(cmd, BreakListCommand):
                if gs.breaks:
                    lines = ["**🔔 쉬는시간 목록**"]
                    for idx, b in enumerate(gs.breaks, 1):
                        nts = b.next_ts or next_occurrence_ts(b.hhmm)
                        ndt = datetime.fromtimestamp(nts, tz=KST)
                        lines.append(
                            f"  {idx}. **{b.label}** {b.hhmm} "
                            f"({fmt_dur(b.duration_sec)}) "
                            f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
                        )
                    replies.append("\n".join(lines))
                else:
                    replies.append("🔔 등록된 쉬는시간이 없습니다.")

            # ── 쉬는시간 삭제 ──
            elif isinstance(cmd, BreakDeleteCommand):
                label = cmd.label
                before = len(gs.breaks)
                gs.breaks = [b for b in gs.breaks if b.label != label]
                removed = before - len(gs.breaks)
                if removed:
                    _save()
                    replies.append(f"✅ 쉬는시간 **{label}** 삭제 ({removed}건)")
                    asyncio.create_task(update_status_panel(gid))
                    if not gs.state_exists():
                        _cancel_voice_worker(gid)
                        asyncio.create_task(ensure_voice_disconnected(gid))
                else:
                    replies.append(f"❌ **{label}** 쉬는시간을 찾을 수 없습니다.")

            # ── 정규쉬는시간 추가 ──
            elif isinstance(cmd, RecurringBreakAddCommand):
                brk = BreakEntry(
                    label=cmd.label,
                    hhmm=cmd.hhmm,
                    duration_sec=cmd.duration_sec,
                    next_ts=next_occurrence_ts(cmd.hhmm),
                )
                gs.recurring_breaks.append(brk)
                _save()
                ndt = datetime.fromtimestamp(brk.next_ts, tz=KST)
                replies.append(
                    f"✅ 정규쉬는시간 **{cmd.label}** 등록 "
                    f"— 매일 {cmd.hhmm} ({fmt_dur(cmd.duration_sec)}) "
                    f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
                )
                ensure_scheduler(gid)
                asyncio.create_task(update_status_panel(gid))

            # ── 정규쉬는시간 목록 ──
            elif isinstance(cmd, RecurringBreakListCommand):
                if gs.recurring_breaks:
                    lines = ["**🔁 정규쉬는시간 목록**"]
                    for idx, b in enumerate(gs.recurring_breaks, 1):
                        nts = b.next_ts or next_occurrence_ts(b.hhmm)
                        ndt = datetime.fromtimestamp(nts, tz=KST)
                        lines.append(
                            f"  {idx}. **{b.label}** 매일 {b.hhmm} "
                            f"({fmt_dur(b.duration_sec)}) "
                            f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
                        )
                    replies.append("\n".join(lines))
                else:
                    replies.append("🔁 등록된 정규쉬는시간이 없습니다.")

            # ── 정규쉬는시간 삭제 ──
            elif isinstance(cmd, RecurringBreakDeleteCommand):
                label = cmd.label
                before = len(gs.recurring_breaks)
                gs.recurring_breaks = [b for b in gs.recurring_breaks if b.label != label]
                removed = before - len(gs.recurring_breaks)
                if removed:
                    _save()
                    replies.append(f"✅ 정규쉬는시간 **{label}** 삭제 ({removed}건)")
                    asyncio.create_task(update_status_panel(gid))
                    if not gs.state_exists():
                        _cancel_voice_worker(gid)
                        asyncio.create_task(ensure_voice_disconnected(gid))
                else:
                    replies.append(f"❌ **{label}** 정규쉬는시간을 찾을 수 없습니다.")

            # ── 프리셋 저장 ──
            elif isinstance(cmd, PresetSaveCommand):
                gs.presets[cmd.name] = cmd.content
                _save()
                replies.append(f"✅ 프리셋 **{cmd.name}** 저장: `{cmd.content}`")

            # ── 프리셋 실행 ──
            elif isinstance(cmd, PresetRunCommand):
                if cmd.name not in gs.presets:
                    replies.append(f"❌ **{cmd.name}** 프리셋 없음")
                else:
                    sub = parse_command(gs.presets[cmd.name])
                    if sub:
                        actions.extend(sub)
                        replies.append(f"▶️ 프리셋 **{cmd.name}** 실행")
                    else:
                        replies.append(f"❌ **{cmd.name}** 프리셋 내용 인식 실패")

            # ── 프리셋 목록 ──
            elif isinstance(cmd, PresetListCommand):
                if gs.presets:
                    lines = ["**📦 프리셋 목록**"]
                    for idx, (pn, pc) in enumerate(gs.presets.items(), 1):
                        lines.append(f"  {idx}. **{pn}** → `{pc}`")
                    replies.append("\n".join(lines))
                else:
                    replies.append("📦 등록된 프리셋이 없습니다.")

            # ── 프리셋 삭제 ──
            elif isinstance(cmd, PresetDeleteCommand):
                if cmd.name in gs.presets:
                    del gs.presets[cmd.name]
                    _save()
                    replies.append(f"✅ 프리셋 **{cmd.name}** 삭제")
                else:
                    replies.append(f"❌ **{cmd.name}** 프리셋 없음")

            # ── 개인 일시정지 ──
            elif isinstance(cmd, PersonalPauseCommand):
                name = cmd.name
                t = gs.timers.get(name)
                if t is None:
                    replies.append(f"❌ **{name}** 타이머 없음")
                elif t.remaining_on_personal_pause is not None:
                    replies.append(f"ℹ️ **{name}** 이미 일시정지 중입니다.")
                else:
                    if gs.pause_until is None:
                        if t.last_accounted_at > 0:
                            _accumulate_stats(gs, name, t.mode, now_ts() - t.last_accounted_at)
                        t.last_accounted_at = now_ts()
                    timer_personal_pause(t, gs)
                    replies.append(
                        f"⏸️ **{name}** 일시정지 "
                        f"(남은 시간 {fmt_mm_ss(t.remaining_on_personal_pause)} 저장)"
                    )
                    asyncio.create_task(update_status_panel(gid))

            # ── 개인 재개 ──
            elif isinstance(cmd, PersonalResumeCommand):
                name = cmd.name
                t = gs.timers.get(name)
                if t is None:
                    replies.append(f"❌ **{name}** 타이머 없음")
                elif t.remaining_on_personal_pause is None:
                    replies.append(f"ℹ️ **{name}** 일시정지 상태가 아닙니다.")
                else:
                    timer_personal_resume(t, gs)
                    t.last_accounted_at = now_ts()
                    if gs.pause_until is not None:
                        replies.append(
                            f"▶️ **{name}** 개인 일시정지 해제 "
                            f"(전체 쉬는시간 종료 후 재개됩니다)"
                        )
                    else:
                        edt = datetime.fromtimestamp(t.phase_end_at, tz=KST)
                        replies.append(
                            f"▶️ **{name}** 재개 → {edt.strftime('%H:%M:%S')}"
                        )
                    asyncio.create_task(update_status_panel(gid))

            # ── 남은시간 수정 ──
            elif isinstance(cmd, SetRemainingCommand):
                name = cmd.name
                new_sec = cmd.seconds
                t = gs.timers.get(name)
                if t is None:
                    replies.append(f"❌ **{name}** 타이머 없음")
                else:
                    if t.remaining_on_personal_pause is not None:
                        t.remaining_on_personal_pause = float(new_sec)
                        replies.append(
                            f"✅ **{name}** 남은시간 → {fmt_dur(new_sec)} (개인 일시정지 중)"
                        )
                    elif t.remaining_on_pause is not None:
                        t.remaining_on_pause = float(new_sec)
                        replies.append(
                            f"✅ **{name}** 남은시간 → {fmt_dur(new_sec)} (전체 일시정지 중)"
                        )
                    else:
                        t.phase_end_at = now_ts() + new_sec
                        edt = datetime.fromtimestamp(t.phase_end_at, tz=KST)
                        replies.append(
                            f"✅ **{name}** 남은시간 → {fmt_dur(new_sec)} "
                            f"(전환 {edt.strftime('%H:%M:%S')})"
                        )
                    asyncio.create_task(update_status_panel(gid))

            # ── 개인 타이머 시작/재설정 ──
            elif isinstance(cmd, SetTimerCommand):
                ts_now = now_ts()
                _as_ts = None
                if cmd.auto_stop_hhmm:
                    _now = datetime.now(KST)
                    _h, _m = map(int, cmd.auto_stop_hhmm.split(":"))
                    _as_ts = _now.replace(hour=_h, minute=_m, second=0, microsecond=0).timestamp()
                entry = Timer(
                    study_sec=cmd.study_sec,
                    rest_sec=cmd.rest_sec,
                    channel_id=cid,
                    mode="study",
                    phase_end_at=ts_now + cmd.study_sec,
                    auto_stop_cycles=cmd.auto_stop_cycles,
                    auto_stop_ts=_as_ts,
                    last_accounted_at=ts_now,
                )
                if gs.pause_until is not None:
                    timer_pause(entry)
                gs.timers[cmd.name] = entry
                _save()
                suffix = ""
                if cmd.auto_stop_cycles:
                    suffix += f" | {cmd.auto_stop_cycles}회 반복"
                if cmd.auto_stop_hhmm:
                    suffix += f" | 오늘 {cmd.auto_stop_hhmm} 종료"
                if gs.pause_until is not None:
                    replies.append(
                        f"✅ **{cmd.name}** 타이머 등록 (현재 일시정지 중 — 재개 후 공부 시작) "
                        f"공부 {cmd.study_sec // 60}분 / 휴식 {cmd.rest_sec // 60}분"
                        + suffix
                    )
                else:
                    edt = datetime.fromtimestamp(entry.phase_end_at, tz=KST)
                    replies.append(
                        f"✅ **{cmd.name}** 타이머 시작 "
                        f"— 공부 {cmd.study_sec // 60}분 / 휴식 {cmd.rest_sec // 60}분, "
                        f"첫 전환 {edt.strftime('%H:%M:%S')}"
                        + suffix
                    )
                ensure_scheduler(gid)
                asyncio.create_task(update_status_panel(gid))

        # 상태가 있는데 음성채널 미설정이면 1회만 안내
        if (
            gs.state_exists()
            and not gs.last_voice_channel_id
            and not gs.voice_notice_sent
        ):
            gs.voice_notice_sent = True
            replies.append(
                "ℹ️ 음성채널에 접속한 뒤 명령을 입력하면 음성 안내가 활성화됩니다."
            )

        await send_split(msg.channel, "\n".join(replies))
