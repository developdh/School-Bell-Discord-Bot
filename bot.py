"""
학교종 Discord 봇
prefix: --학교종
"""
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

# ── Frozen(PyInstaller) 실행 여부 감지 ────────────────────────────────────────
if getattr(sys, "frozen", False):
    # .exe로 실행 중 — .env / state.json / tts_cache / bell.mp3 는 exe 옆에 위치
    _BASE_DIR = Path(sys.executable).parent
    _FFMPEG   = str(Path(sys._MEIPASS) / "ffmpeg.exe")
else:
    _BASE_DIR = Path(__file__).parent
    _FFMPEG   = "ffmpeg"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

KST        = ZoneInfo("Asia/Seoul")
STATE_FILE = _BASE_DIR / "state.json"
PREFIX     = "--학교종"
TTS_CACHE  = _BASE_DIR / "tts_cache"

# ── Runtime state ─────────────────────────────────────────────────────────────
# guild_states[gid] = {
#   "timers": {
#     name: {
#       study_sec, rest_sec, channel_id,
#       mode ("study"|"rest"),                    ← runtime
#       phase_end_at (float ts),                  ← runtime
#       remaining_on_pause (float|None),          ← runtime (전체 쉬는시간)
#       remaining_on_personal_pause (float|None), ← runtime (개인 일시정지)
#       _auto_stop_cycles (int|None),             ← runtime (N회반복)
#       _cycle_count (int),                       ← runtime (현재 완료 사이클)
#       _auto_stop_ts (float|None),               ← runtime (오늘끝 HH:MM)
#     }
#   },
#   "presets": { name: "raw command string", ... },
#   "breaks": [{ label, hhmm, duration_sec, _next_ts }],
#   "recurring_breaks": [{ label, hhmm, duration_sec, _next_ts }],  ← 매일 반복
#   "pause_until": float|None,          ← runtime
#   "last_channel_id": int|None,
#   "last_voice_channel_id": int|None,  ← runtime
#   "pinned_voice_channel_id": int|None, ← 고정 음성채널 (persist)
#   "voice_notice_sent": bool,          ← runtime (음성채널 1회 안내 플래그)
# }
guild_states:  dict[int, dict]          = {}
guild_locks:   dict[int, asyncio.Lock]  = {}
guild_tasks:   dict[int, asyncio.Task]  = {}
voice_queues:  dict[int, asyncio.Queue] = {}  # 길드별 오디오 이벤트 큐
voice_workers: dict[int, asyncio.Task]  = {}  # 길드별 큐 워커 태스크


# ── Persistence ───────────────────────────────────────────────────────────────

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("state.json 로드 실패: %s", e)
        return {}


def save_state() -> None:
    data: dict = {}
    for gid, gs in guild_states.items():
        data[str(gid)] = {
            "last_channel_id": gs.get("last_channel_id"),
            "pinned_voice_channel_id": gs.get("pinned_voice_channel_id"),
            "presets": gs.get("presets", {}),
            "timers": {
                name: {
                    "study_sec":  t["study_sec"],
                    "rest_sec":   t["rest_sec"],
                    "channel_id": t["channel_id"],
                }
                for name, t in gs["timers"].items()
            },
            "breaks": [
                {
                    "label":        b["label"],
                    "hhmm":         b["hhmm"],
                    "duration_sec": b["duration_sec"],
                }
                for b in gs["breaks"]
            ],
            "recurring_breaks": [
                {
                    "label":        b["label"],
                    "hhmm":         b["hhmm"],
                    "duration_sec": b["duration_sec"],
                }
                for b in gs["recurring_breaks"]
            ],
        }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_ts() -> float:
    return datetime.now(KST).timestamp()


def next_occurrence_ts(hhmm: str) -> float:
    """오늘 또는 내일의 HH:MM을 Unix 타임스탬프로 반환."""
    now = datetime.now(KST)
    h, m = map(int, hhmm.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t.timestamp()


def get_guild_state(gid: int) -> dict:
    if gid not in guild_states:
        guild_states[gid] = {
            "timers":                   {},
            "presets":                  {},
            "breaks":                   [],
            "recurring_breaks":         [],
            "pause_until":              None,
            "last_channel_id":          None,
            "last_voice_channel_id":    None,
            "pinned_voice_channel_id":  None,
            "voice_notice_sent":        False,
        }
        guild_locks[gid]  = asyncio.Lock()
        voice_queues[gid] = asyncio.Queue()
    return guild_states[gid]


def fmt_mm_ss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def state_exists(gs: dict) -> bool:
    """타이머 또는 쉬는시간이 1개 이상 있으면 True."""
    return bool(gs.get("timers")) or bool(gs.get("breaks")) or bool(gs.get("recurring_breaks"))


# ── Timer ops ─────────────────────────────────────────────────────────────────

def timer_pause(timer: dict) -> None:
    """남은 시간을 remaining_on_pause에 저장. 개인 일시정지 중이면 건너뜀."""
    if timer.get("remaining_on_personal_pause") is not None:
        return
    timer["remaining_on_pause"] = max(0.0, timer["phase_end_at"] - now_ts())


def timer_resume(timer: dict) -> None:
    """저장된 남은 시간으로 phase_end_at 재설정. 개인 일시정지 중이면 건너뜀."""
    if timer.get("remaining_on_personal_pause") is not None:
        return
    rem = timer.get("remaining_on_pause") or 0.0
    timer["phase_end_at"]       = now_ts() + rem
    timer["remaining_on_pause"] = None


def timer_personal_pause(timer: dict, gs: dict) -> None:
    """개인 일시정지: 현재 남은 시간을 remaining_on_personal_pause에 저장."""
    if gs["pause_until"] is not None and timer.get("remaining_on_pause") is not None:
        # 전체 쉬는시간 중 → remaining_on_pause에 남은 시간이 있음
        timer["remaining_on_personal_pause"] = timer["remaining_on_pause"]
        timer["remaining_on_pause"] = None
    else:
        timer["remaining_on_personal_pause"] = max(0.0, timer["phase_end_at"] - now_ts())


def timer_personal_resume(timer: dict, gs: dict) -> None:
    """개인 일시정지 해제: 저장된 남은 시간 복원."""
    rem = timer.get("remaining_on_personal_pause") or 0.0
    timer["remaining_on_personal_pause"] = None
    if gs["pause_until"] is not None:
        # 전체 쉬는시간 진행 중 → remaining_on_pause로 복원 (전체 끝나면 resume됨)
        timer["remaining_on_pause"] = rem
    else:
        timer["phase_end_at"] = now_ts() + rem


# ── TTS ───────────────────────────────────────────────────────────────────────

async def _make_tts(sentence: str, path: Path) -> bool:
    """TTS 파일 생성. 성공 시 True 반환."""
    # edge-tts 우선 시도
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

    # gTTS fallback
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
    """명시적 캐시 키로 TTS 파일 경로 반환 (없으면 생성)."""
    TTS_CACHE.mkdir(exist_ok=True)
    path = TTS_CACHE / cache_key
    if path.exists() and path.stat().st_size > 0:
        return path
    ok = await _make_tts(sentence, path)
    return path if ok else None


# ── Voice ─────────────────────────────────────────────────────────────────────

async def ensure_voice_connected(gid: int, gs: dict) -> discord.VoiceClient | None:
    """음성채널 연결 상태 확인·유지. 이미 연결되어 있으면 그대로 반환."""
    vc_id = gs.get("last_voice_channel_id")
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
        # 동시 connect 경쟁 발생 시 기존 연결 재조회
        return discord.utils.get(bot.voice_clients, guild=vc_channel.guild)  # type: ignore[return-value]
    except Exception:
        log.exception("음성채널 연결 실패 guild=%d", gid)
        return None


async def ensure_voice_disconnected(gid: int) -> None:
    """해당 길드의 음성채널 연결을 해제."""
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
    """단일 파일 재생 후 완료 대기 (최대 5분 타임아웃)."""
    if not path.exists() or path.stat().st_size == 0:
        log.warning("재생 파일 없거나 크기 0: %s", path)
        return

    if vc.is_playing():
        log.warning("이미 재생 중 — stop 후 재생: %s", path.name)
        vc.stop()
        await asyncio.sleep(0.1)

    loop    = asyncio.get_running_loop()
    done    = loop.create_future()

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
    """
    길드별 오디오 큐 워커.
    큐에서 (sentence, cache_key) 를 꺼내 벨 → TTS 순서로 순차 재생.
    재생 도중 다른 이벤트는 큐에 쌓여 대기.
    """
    log.info("음성 워커 시작 guild=%d", gid)
    q = voice_queues[gid]
    try:
        while True:
            sentence, cache_key = await q.get()
            try:
                gs = guild_states.get(gid)
                if gs is None or not state_exists(gs):
                    log.debug("상태 없음, 오디오 스킵 guild=%d", gid)
                    continue

                # TTS 생성 (느리므로 VC 연결 전에 수행)
                tts_path = await _get_tts_path_keyed(sentence, cache_key)

                # VC 연결 확인
                vc = await ensure_voice_connected(gid, gs)
                if vc is None:
                    log.debug("음성 연결 불가, 오디오 스킵 guild=%d", gid)
                    continue

                # 1) 벨
                for bell in (_BASE_DIR / "bell.mp3", _BASE_DIR / "bell.wav"):
                    if bell.exists():
                        await _play_voice_audio(vc, bell)
                        break
                else:
                    log.debug("bell.mp3 / bell.wav 없음, 벨 스킵")

                # 2) TTS
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
    """워커 태스크가 살아있지 않으면 새로 시작."""
    w = voice_workers.get(gid)
    if w is None or w.done():
        if gid not in voice_queues:
            voice_queues[gid] = asyncio.Queue()
        voice_workers[gid] = asyncio.create_task(_voice_worker(gid))


def _cancel_voice_worker(gid: int) -> None:
    """워커 태스크를 취소."""
    w = voice_workers.pop(gid, None)
    if w and not w.done():
        w.cancel()


def play_event_audio(gid: int, sentence: str, cache_key: str) -> None:
    """이벤트 오디오(벨+TTS)를 큐에 추가. 워커가 순차 재생."""
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


async def _break_channels(gs: dict) -> list[discord.TextChannel]:
    """쉬는시간 알림을 보낼 채널 목록(중복 제거)."""
    seen: set[int] = set()
    result: list[discord.TextChannel] = []
    ids = {t["channel_id"] for t in gs["timers"].values()}
    if gs.get("last_channel_id"):
        ids.add(gs["last_channel_id"])
    for cid in ids:
        if cid not in seen:
            ch = await _get_channel(cid)
            if ch:
                result.append(ch)
                seen.add(cid)
    return result


async def notify_transition(
    gid: int, cid: int, name: str, mode: str
) -> None:
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
    gid: int, gs: dict, brk: dict, end_ts: float, extending: bool
) -> None:
    end_dt = datetime.fromtimestamp(end_ts, tz=KST)
    if extending:
        msg = (
            f"⏸️ **{brk['label']}** — 일시정지 연장 "
            f"→ {end_dt.strftime('%H:%M:%S')}까지"
        )
    else:
        msg = (
            f"⏸️ **{brk['label']}** 쉬는시간! "
            f"{_fmt_dur(brk['duration_sec'])} 일시정지 "
            f"(→ {end_dt.strftime('%H:%M:%S')} 재개)"
        )
    for ch in await _break_channels(gs):
        await ch.send(msg)
    if not extending:
        sentence   = f"{brk['label']} 시작."
        safe_label = re.sub(r"[^\w가-힣]", "_", brk["label"])[:20]
        cache_key  = f"{gid}_brk_{safe_label}.mp3"
        play_event_audio(gid, sentence, cache_key)


async def notify_resume(gid: int, gs: dict) -> None:
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
                for brk in gs["breaks"] + gs["recurring_breaks"]:
                    bt = brk.get("_next_ts")
                    if bt is None or ts < bt:
                        continue
                    end_ts   = ts + brk["duration_sec"]
                    already  = gs["pause_until"] is not None
                    if not already or gs["pause_until"] < end_ts:
                        if not already:
                            for t in gs["timers"].values():
                                timer_pause(t)
                        gs["pause_until"] = end_ts
                        await notify_break_event(gid, gs, brk, end_ts, already)
                    brk["_next_ts"] = next_occurrence_ts(brk["hhmm"])

                # 2) 일시정지 종료 체크
                if gs["pause_until"] is not None and ts >= gs["pause_until"]:
                    gs["pause_until"] = None
                    for t in gs["timers"].values():
                        timer_resume(t)
                    await notify_resume(gid, gs)

                # 3) 개인 타이머 전환 체크 (pause 중 아닐 때만)
                if gs["pause_until"] is None:
                    for name, t in list(gs["timers"].items()):
                        if t.get("remaining_on_personal_pause") is not None:
                            continue

                        # Auto-stop: 시간 제한
                        if t.get("_auto_stop_ts") is not None and ts >= t["_auto_stop_ts"]:
                            cid_as = t["channel_id"]
                            del gs["timers"][name]
                            save_state()
                            ch = await _get_channel(cid_as)
                            if ch:
                                await ch.send(f"🏁 **{name}** 시간 도달 → 자동 종료")
                            safe = re.sub(r"[^\w가-힣]", "_", name)[:20]
                            play_event_audio(gid, f"{name} 자동 종료.", f"{gid}_as_{safe}.mp3")
                            if not state_exists(gs):
                                _cancel_voice_worker(gid)
                                asyncio.create_task(ensure_voice_disconnected(gid))
                            continue

                        if ts >= t["phase_end_at"]:
                            new_mode = "rest" if t["mode"] == "study" else "study"

                            # Auto-stop: 반복 횟수 (rest→study = 1사이클 완료)
                            if new_mode == "study" and t.get("_auto_stop_cycles") is not None:
                                t["_cycle_count"] = t.get("_cycle_count", 0) + 1
                                if t["_cycle_count"] >= t["_auto_stop_cycles"]:
                                    cycles = t["_auto_stop_cycles"]
                                    cid_as = t["channel_id"]
                                    del gs["timers"][name]
                                    save_state()
                                    ch = await _get_channel(cid_as)
                                    if ch:
                                        await ch.send(
                                            f"🏁 **{name}** "
                                            f"{cycles}회 반복 완료 → 자동 종료"
                                        )
                                    safe = re.sub(r"[^\w가-힣]", "_", name)[:20]
                                    play_event_audio(gid, f"{name} 자동 종료.", f"{gid}_as_{safe}.mp3")
                                    if not state_exists(gs):
                                        _cancel_voice_worker(gid)
                                        asyncio.create_task(ensure_voice_disconnected(gid))
                                    continue

                            overshoot = ts - t["phase_end_at"]
                            t["mode"]         = new_mode
                            t["phase_end_at"] = ts + t[f"{new_mode}_sec"] - overshoot
                            await notify_transition(
                                gid, t["channel_id"], name, new_mode
                            )

                # 4) 음성채널 연결 유지
                if state_exists(gs) and gs.get("last_voice_channel_id"):
                    _ensure_voice_worker(gid)
                    # 워커가 큐를 처리 중이 아닐 때만 직접 연결 확인
                    q = voice_queues.get(gid)
                    if q is not None and q.empty():
                        asyncio.create_task(ensure_voice_connected(gid, gs))
                elif not state_exists(gs):
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


# ── Parser ────────────────────────────────────────────────────────────────────

_RE_TIME   = re.compile(r"^(\d+)(초|분|시간)(공부|휴식)$")
_RE_DUR    = re.compile(r"^(\d+)(초|분|시간)$")
_RE_HHMM   = re.compile(r"^\d{1,2}:\d{2}$")
_RE_REPEAT = re.compile(r"^(\d+)회반복$")


def _unit_to_sec(n: int, unit: str) -> int:
    if unit == "초":   return n
    if unit == "시간": return n * 3600
    return n * 60  # 분


def _fmt_dur(sec: int) -> str:
    """초 → 사람이 읽기 좋은 문자열 (예: '30초', '10분', '1시간 30분')"""
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if h: parts.append(f"{h}시간")
    if m: parts.append(f"{m}분")
    if s: parts.append(f"{s}초")
    return " ".join(parts) if parts else "0초"


def _time_tok(s: str) -> tuple[str, int] | None:
    """'10분공부' → ('study', 600), '30초휴식' → ('rest', 30), '1시간공부' → ('study', 3600)"""
    m = _RE_TIME.match(s)
    if not m:
        return None
    return ("study" if m.group(3) == "공부" else "rest"), _unit_to_sec(int(m.group(1)), m.group(2))


def _dur_tok(s: str) -> int | None:
    """'20분' → 1200, '30초' → 30, '1시간' → 3600"""
    m = _RE_DUR.match(s)
    return _unit_to_sec(int(m.group(1)), m.group(2)) if m else None


def parse_command(raw: str) -> list[dict]:
    """
    공백 토큰 기반 왼쪽부터 순차 파싱.
    반환: 액션 리스트

    우선순위:
      0) "도움말" / "help"               → help
      1) "상태"                          → status
      2) "종료"                          → shutdown_all
      2a) "음성채널 고정"                → voice_pin
      2b) "음성채널 해제"                → voice_unpin
      2c) "프리셋 저장 [이름] [내용...]" → preset_save
      2d) "프리셋 실행 [이름]"          → preset_run
      2e) "프리셋 목록"                 → preset_list
      2f) "프리셋 삭제 [이름]"          → preset_delete
      3) "쉬는시간 끝"                   → break_end
      3a) "쉬는시간 목록"               → break_list
      3b) "쉬는시간 삭제 [라벨]"        → break_delete
      3b-2) "정규쉬는시간 추가/목록/삭제" → recurring_break_*
      3c) "일시정지 [이름]"             → personal_pause
      3d) "재개 [이름]"                 → personal_resume
      3e) "남은시간 [이름] [N분]"       → set_remaining
      4) "[이름] 종료"                   → stop
      5) "쉬는시간 [라벨] HH:MM [N분]"  → break
      6) "[이름] [N분공부] [M분휴식] [N회반복]? [오늘끝 HH:MM]?"  → timer
    """
    tokens = raw.strip().split()
    actions: list[dict] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # 0) 도움말 / help
        if tok == "도움말" or tok.lower() == "help":
            actions.append({"type": "help"})
            i += 1
            continue

        # 1) 상태
        if tok == "상태":
            actions.append({"type": "status"})
            i += 1
            continue

        # 2) 전체 종료 (단독 "종료" 토큰)
        if tok == "종료":
            actions.append({"type": "shutdown_all"})
            i += 1
            continue

        # 2a) 음성채널 고정 / 해제
        if tok == "음성채널" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub == "고정":
                actions.append({"type": "voice_pin"})
                i += 2
                continue
            if sub == "해제":
                actions.append({"type": "voice_unpin"})
                i += 2
                continue

        # 2c-f) 프리셋
        if tok == "프리셋" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub == "저장" and i + 3 < len(tokens):
                pname = tokens[i + 2]
                content = " ".join(tokens[i + 3:])
                actions.append({"type": "preset_save", "name": pname, "content": content})
                i = len(tokens)
                continue
            if sub == "실행" and i + 2 < len(tokens):
                actions.append({"type": "preset_run", "name": tokens[i + 2]})
                i += 3
                continue
            if sub == "목록":
                actions.append({"type": "preset_list"})
                i += 2
                continue
            if sub == "삭제" and i + 2 < len(tokens):
                actions.append({"type": "preset_delete", "name": tokens[i + 2]})
                i += 3
                continue

        # 3) 쉬는시간 강제 종료 ("쉬는시간 끝")
        if tok == "쉬는시간" and i + 1 < len(tokens) and tokens[i + 1] == "끝":
            actions.append({"type": "break_end"})
            i += 2
            continue

        # 3a) 쉬는시간 목록
        if tok == "쉬는시간" and i + 1 < len(tokens) and tokens[i + 1] == "목록":
            actions.append({"type": "break_list"})
            i += 2
            continue

        # 3b) 쉬는시간 삭제 [라벨]
        if tok == "쉬는시간" and i + 2 < len(tokens) and tokens[i + 1] == "삭제":
            actions.append({"type": "break_delete", "label": tokens[i + 2]})
            i += 3
            continue

        # 3b-2) 정규쉬는시간 추가/목록/삭제
        if tok == "정규쉬는시간" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub == "추가" and i + 4 < len(tokens):
                label, hhmm, dur_s = tokens[i + 2], tokens[i + 3], tokens[i + 4]
                if _RE_HHMM.match(hhmm):
                    dur = _dur_tok(dur_s)
                    if dur is not None:
                        actions.append({
                            "type":         "recurring_break_add",
                            "label":        label,
                            "hhmm":         hhmm,
                            "duration_sec": dur,
                        })
                        i += 5
                        continue
            if sub == "목록":
                actions.append({"type": "recurring_break_list"})
                i += 2
                continue
            if sub == "삭제" and i + 2 < len(tokens):
                actions.append({"type": "recurring_break_delete", "label": tokens[i + 2]})
                i += 3
                continue

        # 3c) 일시정지 [이름]
        if tok == "일시정지" and i + 1 < len(tokens):
            actions.append({"type": "personal_pause", "name": tokens[i + 1]})
            i += 2
            continue

        # 3d) 재개 [이름]
        if tok == "재개" and i + 1 < len(tokens):
            actions.append({"type": "personal_resume", "name": tokens[i + 1]})
            i += 2
            continue

        # 3e) 남은시간 [이름] [N분]
        if tok == "남은시간" and i + 2 < len(tokens):
            dur = _dur_tok(tokens[i + 2])
            if dur is not None:
                actions.append({"type": "set_remaining", "name": tokens[i + 1], "seconds": dur})
                i += 3
                continue

        # 4) [이름] 종료
        if i + 1 < len(tokens) and tokens[i + 1] == "종료":
            actions.append({"type": "stop", "name": tok})
            i += 2
            continue

        # 5) 쉬는시간 [라벨] HH:MM [N분]
        if tok == "쉬는시간" and i + 3 < len(tokens):
            label, hhmm, dur_s = tokens[i + 1], tokens[i + 2], tokens[i + 3]
            if _RE_HHMM.match(hhmm):
                dur = _dur_tok(dur_s)
                if dur is not None:
                    actions.append({
                        "type":         "break",
                        "label":        label,
                        "hhmm":         hhmm,
                        "duration_sec": dur,
                    })
                    i += 4
                    continue

        # 6) [이름] [N분공부] [M분휴식] [N회반복]? [오늘끝 HH:MM]?
        if i + 2 < len(tokens):
            r1 = _time_tok(tokens[i + 1])
            r2 = _time_tok(tokens[i + 2])
            if r1 and r2 and r1[0] != r2[0]:
                study = r1[1] if r1[0] == "study" else r2[1]
                rest  = r2[1] if r2[0] == "rest"  else r1[1]
                i += 3
                # optional trailing modifiers
                auto_cycles   = None
                auto_end_hhmm = None
                while i < len(tokens):
                    rm = _RE_REPEAT.match(tokens[i])
                    if rm:
                        auto_cycles = int(rm.group(1))
                        i += 1
                        continue
                    if tokens[i] == "오늘끝" and i + 1 < len(tokens) and _RE_HHMM.match(tokens[i + 1]):
                        auto_end_hhmm = tokens[i + 1]
                        i += 2
                        continue
                    break
                act_d: dict = {
                    "type":      "timer",
                    "name":      tok,
                    "study_sec": study,
                    "rest_sec":  rest,
                }
                if auto_cycles is not None:
                    act_d["auto_stop_cycles"] = auto_cycles
                if auto_end_hhmm is not None:
                    act_d["auto_stop_hhmm"] = auto_end_hhmm
                actions.append(act_d)
                continue

        i += 1  # 인식 불가 토큰 → 건너뜀

    return actions


# ── Status builder ────────────────────────────────────────────────────────────

def build_status(gs: dict) -> str:
    ts = now_ts()
    lines: list[str] = []

    # 일시정지 배너
    if gs["pause_until"] is not None:
        rem = gs["pause_until"] - ts
        edt = datetime.fromtimestamp(gs["pause_until"], tz=KST)
        lines.append(
            f"⏸️ **일시정지 중** — {edt.strftime('%H:%M:%S')} 재개 "
            f"(남은 시간 : {fmt_mm_ss(rem)})"
        )

    # 개인 타이머
    if gs["timers"]:
        lines.append("**📋 개인 타이머**")
        for name, t in gs["timers"].items():
            ml = "공부" if t["mode"] == "study" else "휴식"
            if t.get("remaining_on_personal_pause") is not None:
                rem   = t["remaining_on_personal_pause"]
                end_s = "(개인 일시정지)"
            elif gs["pause_until"] is not None and t.get("remaining_on_pause") is not None:
                rem   = t["remaining_on_pause"]
                end_s = "(일시정지)"
            else:
                rem   = t["phase_end_at"] - ts
                end_s = datetime.fromtimestamp(t["phase_end_at"], tz=KST).strftime("%H:%M:%S")
            auto_info = ""
            if t.get("_auto_stop_cycles") is not None:
                auto_info += f" [{t.get('_cycle_count', 0)}/{t['_auto_stop_cycles']}회]"
            if t.get("_auto_stop_ts") is not None:
                _edt = datetime.fromtimestamp(t["_auto_stop_ts"], tz=KST)
                auto_info += f" [끝 {_edt.strftime('%H:%M')}]"
            lines.append(f"  • **{name}** [{ml}] 남은 시간 : {fmt_mm_ss(rem)} → {end_s}{auto_info}")
    else:
        lines.append("📋 등록된 타이머 없음")

    # 쉬는시간 목록
    if gs["breaks"]:
        lines.append("**🔔 쉬는시간 목록**")
        for b in gs["breaks"]:
            nts = b.get("_next_ts") or next_occurrence_ts(b["hhmm"])
            ndt = datetime.fromtimestamp(nts, tz=KST)
            lines.append(
                f"  • **{b['label']}** → {ndt.strftime('%m/%d %H:%M')} "
                f"({_fmt_dur(b['duration_sec'])})"
            )
    else:
        lines.append("🔔 등록된 쉬는시간 없음")

    # 정규쉬는시간 목록
    if gs["recurring_breaks"]:
        lines.append("**🔁 정규쉬는시간 목록**")
        for b in gs["recurring_breaks"]:
            nts = b.get("_next_ts") or next_occurrence_ts(b["hhmm"])
            ndt = datetime.fromtimestamp(nts, tz=KST)
            lines.append(
                f"  • **{b['label']}** 매일 {b['hhmm']} "
                f"({_fmt_dur(b['duration_sec'])}) "
                f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
            )

    # 음성채널 고정 표시
    pvc = gs.get("pinned_voice_channel_id")
    if pvc:
        lines.append(f"🔊 음성채널 고정: <#{pvc}>")

    return "\n".join(lines)


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
        "**12) 상태 출력**\n"
        "```\n"
        "--학교종 상태\n"
        "```\n"
        "\n"
        "**13) 도움말**\n"
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
        gs["last_channel_id"] = data.get("last_channel_id")
        gs["pinned_voice_channel_id"] = data.get("pinned_voice_channel_id")
        if gs["pinned_voice_channel_id"]:
            gs["last_voice_channel_id"] = gs["pinned_voice_channel_id"]
        gs["presets"] = data.get("presets", {})

        # 쉬는시간 복구
        for b in data.get("breaks", []):
            gs["breaks"].append({
                "label":        b["label"],
                "hhmm":         b["hhmm"],
                "duration_sec": b["duration_sec"],
                "_next_ts":     next_occurrence_ts(b["hhmm"]),
            })

        # 정규쉬는시간 복구 (다음 예정 시각만 재계산, 소급 적용 없음)
        for b in data.get("recurring_breaks", []):
            gs["recurring_breaks"].append({
                "label":        b["label"],
                "hhmm":         b["hhmm"],
                "duration_sec": b["duration_sec"],
                "_next_ts":     next_occurrence_ts(b["hhmm"]),
            })

        # 타이머 복구 — mode=study, now부터 리셋
        for name, td in data.get("timers", {}).items():
            gs["timers"][name] = {
                "study_sec":                   td["study_sec"],
                "rest_sec":                    td["rest_sec"],
                "channel_id":                  td["channel_id"],
                "mode":                        "study",
                "phase_end_at":                ts + td["study_sec"],
                "remaining_on_pause":          None,
                "remaining_on_personal_pause": None,
                "_auto_stop_cycles":           None,
                "_cycle_count":                0,
                "_auto_stop_ts":               None,
            }

        if gs["timers"] or gs["breaks"] or gs["recurring_breaks"]:
            ensure_scheduler(gid)

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

    # 음성채널 추적: 고정 채널이 없을 때만 사용자의 음성채널로 갱신
    if (
        not gs.get("pinned_voice_channel_id")
        and msg.guild
        and hasattr(msg.author, "voice")
        and msg.author.voice
        and msg.author.voice.channel
    ):
        gs["last_voice_channel_id"] = msg.author.voice.channel.id

    actions = parse_command(raw)
    if not actions:
        await msg.channel.send("❌ 명령어를 인식할 수 없습니다.")
        return

    async with lock:
        gs["last_channel_id"] = cid
        replies: list[str] = []

        for act in actions:
            atype = act["type"]

            # ── 도움말 ──
            if atype == "help":
                replies.append(build_help())

            # ── 상태 ──
            elif atype == "status":
                replies.append(build_status(gs))

            # ── 전체 종료 ──
            elif atype == "shutdown_all":
                gs["timers"].clear()
                gs["breaks"].clear()
                gs["recurring_breaks"].clear()
                gs["pause_until"]              = None
                gs["pinned_voice_channel_id"]  = None
                gs["last_voice_channel_id"]    = None
                gs["voice_notice_sent"]        = False
                save_state()
                task = guild_tasks.pop(gid, None)
                if task:
                    task.cancel()
                _cancel_voice_worker(gid)
                asyncio.create_task(ensure_voice_disconnected(gid))
                replies.append("✅ 전체 종료: 모든 타이머/쉬는시간 중지")

            # ── 쉬는시간 강제 종료 ──
            elif atype == "break_end":
                if gs["pause_until"] is None:
                    replies.append("ℹ️ 현재 일시정지 중이 아닙니다")
                else:
                    gs["pause_until"] = None
                    for t in gs["timers"].values():
                        timer_resume(t)
                    await notify_resume(gid, gs)

            # ── 음성채널 고정 ──
            elif atype == "voice_pin":
                if (
                    msg.guild
                    and hasattr(msg.author, "voice")
                    and msg.author.voice
                    and msg.author.voice.channel
                ):
                    vc_ch = msg.author.voice.channel
                    gs["pinned_voice_channel_id"] = vc_ch.id
                    gs["last_voice_channel_id"]   = vc_ch.id
                    save_state()
                    replies.append(f"✅ 음성채널 **{vc_ch.name}** 고정")
                else:
                    replies.append("❌ 음성채널에 먼저 접속해주세요.")

            # ── 음성채널 해제 ──
            elif atype == "voice_unpin":
                if gs.get("pinned_voice_channel_id"):
                    gs["pinned_voice_channel_id"] = None
                    gs["last_voice_channel_id"]   = None
                    save_state()
                    _cancel_voice_worker(gid)
                    asyncio.create_task(ensure_voice_disconnected(gid))
                    replies.append("✅ 음성채널 고정 해제")
                else:
                    replies.append("ℹ️ 고정된 음성채널이 없습니다.")

            # ── 종료 ──
            elif atype == "stop":
                name = act["name"]
                if name in gs["timers"]:
                    del gs["timers"][name]
                    save_state()
                    replies.append(f"✅ **{name}** 타이머 종료")
                    # 상태가 없어졌으면 음성도 해제
                    if not state_exists(gs):
                        _cancel_voice_worker(gid)
                        asyncio.create_task(ensure_voice_disconnected(gid))
                else:
                    replies.append(f"❌ **{name}** 타이머 없음")

            # ── 쉬는시간 등록 ──
            elif atype == "break":
                brk = {
                    "label":        act["label"],
                    "hhmm":         act["hhmm"],
                    "duration_sec": act["duration_sec"],
                    "_next_ts":     next_occurrence_ts(act["hhmm"]),
                }
                gs["breaks"].append(brk)
                save_state()
                ndt = datetime.fromtimestamp(brk["_next_ts"], tz=KST)
                replies.append(
                    f"✅ 쉬는시간 **{act['label']}** 등록 "
                    f"— {ndt.strftime('%m/%d %H:%M')} ({_fmt_dur(act['duration_sec'])})"
                )
                ensure_scheduler(gid)

            # ── 쉬는시간 목록 ──
            elif atype == "break_list":
                if gs["breaks"]:
                    lines = ["**🔔 쉬는시간 목록**"]
                    for idx, b in enumerate(gs["breaks"], 1):
                        nts = b.get("_next_ts") or next_occurrence_ts(b["hhmm"])
                        ndt = datetime.fromtimestamp(nts, tz=KST)
                        lines.append(
                            f"  {idx}. **{b['label']}** {b['hhmm']} "
                            f"({_fmt_dur(b['duration_sec'])}) "
                            f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
                        )
                    replies.append("\n".join(lines))
                else:
                    replies.append("🔔 등록된 쉬는시간이 없습니다.")

            # ── 쉬는시간 삭제 ──
            elif atype == "break_delete":
                label = act["label"]
                before = len(gs["breaks"])
                gs["breaks"] = [b for b in gs["breaks"] if b["label"] != label]
                removed = before - len(gs["breaks"])
                if removed:
                    save_state()
                    replies.append(f"✅ 쉬는시간 **{label}** 삭제 ({removed}건)")
                    if not state_exists(gs):
                        _cancel_voice_worker(gid)
                        asyncio.create_task(ensure_voice_disconnected(gid))
                else:
                    replies.append(f"❌ **{label}** 쉬는시간을 찾을 수 없습니다.")

            # ── 정규쉬는시간 추가 ──
            elif atype == "recurring_break_add":
                brk = {
                    "label":        act["label"],
                    "hhmm":         act["hhmm"],
                    "duration_sec": act["duration_sec"],
                    "_next_ts":     next_occurrence_ts(act["hhmm"]),
                }
                gs["recurring_breaks"].append(brk)
                save_state()
                ndt = datetime.fromtimestamp(brk["_next_ts"], tz=KST)
                replies.append(
                    f"✅ 정규쉬는시간 **{act['label']}** 등록 "
                    f"— 매일 {act['hhmm']} ({_fmt_dur(act['duration_sec'])}) "
                    f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
                )
                ensure_scheduler(gid)

            # ── 정규쉬는시간 목록 ──
            elif atype == "recurring_break_list":
                if gs["recurring_breaks"]:
                    lines = ["**🔁 정규쉬는시간 목록**"]
                    for idx, b in enumerate(gs["recurring_breaks"], 1):
                        nts = b.get("_next_ts") or next_occurrence_ts(b["hhmm"])
                        ndt = datetime.fromtimestamp(nts, tz=KST)
                        lines.append(
                            f"  {idx}. **{b['label']}** 매일 {b['hhmm']} "
                            f"({_fmt_dur(b['duration_sec'])}) "
                            f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
                        )
                    replies.append("\n".join(lines))
                else:
                    replies.append("🔁 등록된 정규쉬는시간이 없습니다.")

            # ── 정규쉬는시간 삭제 ──
            elif atype == "recurring_break_delete":
                label = act["label"]
                before = len(gs["recurring_breaks"])
                gs["recurring_breaks"] = [b for b in gs["recurring_breaks"] if b["label"] != label]
                removed = before - len(gs["recurring_breaks"])
                if removed:
                    save_state()
                    replies.append(f"✅ 정규쉬는시간 **{label}** 삭제 ({removed}건)")
                    if not state_exists(gs):
                        _cancel_voice_worker(gid)
                        asyncio.create_task(ensure_voice_disconnected(gid))
                else:
                    replies.append(f"❌ **{label}** 정규쉬는시간을 찾을 수 없습니다.")

            # ── 프리셋 저장 ──
            elif atype == "preset_save":
                pname = act["name"]
                gs.setdefault("presets", {})[pname] = act["content"]
                save_state()
                replies.append(f"✅ 프리셋 **{pname}** 저장: `{act['content']}`")

            # ── 프리셋 실행 ──
            elif atype == "preset_run":
                pname = act["name"]
                presets = gs.get("presets", {})
                if pname not in presets:
                    replies.append(f"❌ **{pname}** 프리셋 없음")
                else:
                    sub = parse_command(presets[pname])
                    if sub:
                        actions.extend(sub)
                        replies.append(f"▶️ 프리셋 **{pname}** 실행")
                    else:
                        replies.append(f"❌ **{pname}** 프리셋 내용 인식 실패")

            # ── 프리셋 목록 ──
            elif atype == "preset_list":
                presets = gs.get("presets", {})
                if presets:
                    lines = ["**📦 프리셋 목록**"]
                    for idx, (pn, pc) in enumerate(presets.items(), 1):
                        lines.append(f"  {idx}. **{pn}** → `{pc}`")
                    replies.append("\n".join(lines))
                else:
                    replies.append("📦 등록된 프리셋이 없습니다.")

            # ── 프리셋 삭제 ──
            elif atype == "preset_delete":
                pname = act["name"]
                presets = gs.get("presets", {})
                if pname in presets:
                    del presets[pname]
                    save_state()
                    replies.append(f"✅ 프리셋 **{pname}** 삭제")
                else:
                    replies.append(f"❌ **{pname}** 프리셋 없음")

            # ── 개인 일시정지 ──
            elif atype == "personal_pause":
                name = act["name"]
                t = gs["timers"].get(name)
                if t is None:
                    replies.append(f"❌ **{name}** 타이머 없음")
                elif t.get("remaining_on_personal_pause") is not None:
                    replies.append(f"ℹ️ **{name}** 이미 일시정지 중입니다.")
                else:
                    timer_personal_pause(t, gs)
                    replies.append(
                        f"⏸️ **{name}** 일시정지 "
                        f"(남은 시간 {fmt_mm_ss(t['remaining_on_personal_pause'])} 저장)"
                    )

            # ── 개인 재개 ──
            elif atype == "personal_resume":
                name = act["name"]
                t = gs["timers"].get(name)
                if t is None:
                    replies.append(f"❌ **{name}** 타이머 없음")
                elif t.get("remaining_on_personal_pause") is None:
                    replies.append(f"ℹ️ **{name}** 일시정지 상태가 아닙니다.")
                else:
                    timer_personal_resume(t, gs)
                    if gs["pause_until"] is not None:
                        replies.append(
                            f"▶️ **{name}** 개인 일시정지 해제 "
                            f"(전체 쉬는시간 종료 후 재개됩니다)"
                        )
                    else:
                        edt = datetime.fromtimestamp(t["phase_end_at"], tz=KST)
                        replies.append(
                            f"▶️ **{name}** 재개 → {edt.strftime('%H:%M:%S')}"
                        )

            # ── 남은시간 수정 ──
            elif atype == "set_remaining":
                name = act["name"]
                new_sec = act["seconds"]
                t = gs["timers"].get(name)
                if t is None:
                    replies.append(f"❌ **{name}** 타이머 없음")
                else:
                    if t.get("remaining_on_personal_pause") is not None:
                        t["remaining_on_personal_pause"] = float(new_sec)
                        replies.append(
                            f"✅ **{name}** 남은시간 → {_fmt_dur(new_sec)} (개인 일시정지 중)"
                        )
                    elif t.get("remaining_on_pause") is not None:
                        t["remaining_on_pause"] = float(new_sec)
                        replies.append(
                            f"✅ **{name}** 남은시간 → {_fmt_dur(new_sec)} (전체 일시정지 중)"
                        )
                    else:
                        t["phase_end_at"] = now_ts() + new_sec
                        edt = datetime.fromtimestamp(t["phase_end_at"], tz=KST)
                        replies.append(
                            f"✅ **{name}** 남은시간 → {_fmt_dur(new_sec)} "
                            f"(전환 {edt.strftime('%H:%M:%S')})"
                        )

            # ── 개인 타이머 시작/재설정 ──
            elif atype == "timer":
                ts_now = now_ts()
                # 오늘끝 HH:MM → 오늘 해당 시각 타임스탬프
                _as_ts = None
                if act.get("auto_stop_hhmm"):
                    _now = datetime.now(KST)
                    _h, _m = map(int, act["auto_stop_hhmm"].split(":"))
                    _as_ts = _now.replace(hour=_h, minute=_m, second=0, microsecond=0).timestamp()
                entry: dict = {
                    "study_sec":                   act["study_sec"],
                    "rest_sec":                    act["rest_sec"],
                    "channel_id":                  cid,
                    "mode":                        "study",
                    "phase_end_at":                ts_now + act["study_sec"],
                    "remaining_on_pause":          None,
                    "remaining_on_personal_pause": None,
                    "_auto_stop_cycles":           act.get("auto_stop_cycles"),
                    "_cycle_count":                0,
                    "_auto_stop_ts":               _as_ts,
                }
                if gs["pause_until"] is not None:
                    timer_pause(entry)
                gs["timers"][act["name"]] = entry
                save_state()
                # 자동종료 조건 suffix
                suffix = ""
                if act.get("auto_stop_cycles"):
                    suffix += f" | {act['auto_stop_cycles']}회 반복"
                if act.get("auto_stop_hhmm"):
                    suffix += f" | 오늘 {act['auto_stop_hhmm']} 종료"
                if gs["pause_until"] is not None:
                    replies.append(
                        f"✅ **{act['name']}** 타이머 등록 (현재 일시정지 중 — 재개 후 공부 시작) "
                        f"공부 {act['study_sec'] // 60}분 / 휴식 {act['rest_sec'] // 60}분"
                        + suffix
                    )
                else:
                    edt = datetime.fromtimestamp(entry["phase_end_at"], tz=KST)
                    replies.append(
                        f"✅ **{act['name']}** 타이머 시작 "
                        f"— 공부 {act['study_sec'] // 60}분 / 휴식 {act['rest_sec'] // 60}분, "
                        f"첫 전환 {edt.strftime('%H:%M:%S')}"
                        + suffix
                    )
                ensure_scheduler(gid)

        # 상태가 있는데 음성채널 미설정이면 1회만 안내
        if (
            state_exists(gs)
            and not gs.get("last_voice_channel_id")
            and not gs.get("voice_notice_sent")
        ):
            gs["voice_notice_sent"] = True
            replies.append(
                "ℹ️ 음성채널에 접속한 뒤 명령을 입력하면 음성 안내가 활성화됩니다."
            )

        await send_split(msg.channel, "\n".join(replies))


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # .env 파일 로드 (exe 옆 또는 소스 디렉토리)
    _env_file = _BASE_DIR / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        # 첫 실행 시 토큰 입력 후 .env 에 저장
        token = input("Discord 봇 토큰을 입력하세요: ").strip()
        if token:
            _env_file.write_text(f"DISCORD_TOKEN={token}\n", encoding="utf-8")
            print(f".env 저장 완료 ({_env_file})")
        else:
            raise SystemExit("토큰이 없습니다.")
    bot.run(token, log_handler=None)
