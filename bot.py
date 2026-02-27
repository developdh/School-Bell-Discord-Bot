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

# ── PyNaCl debug (frozen exe) ────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _mp = sys._MEIPASS
    print(f"[DIAG] _MEIPASS: {_mp}")
    # list nacl/sodium related files in bundle
    for root, dirs, files in os.walk(_mp):
        for f in files:
            if "nacl" in f.lower() or "sodium" in f.lower():
                print(f"[DIAG] file: {os.path.join(root, f)}")
    # test nacl import chain
    try:
        import nacl
        print(f"[DIAG] nacl OK: {nacl.__file__}")
    except Exception as e:
        print(f"[DIAG] nacl FAIL: {e}")
    try:
        import nacl._sodium
        print(f"[DIAG] nacl._sodium OK: {nacl._sodium.__file__}")
    except Exception as e:
        print(f"[DIAG] nacl._sodium FAIL: {e}")
    try:
        import nacl.secret
        print(f"[DIAG] nacl.secret OK")
    except Exception as e:
        print(f"[DIAG] nacl.secret FAIL: {e}")
    try:
        import nacl.utils
        print(f"[DIAG] nacl.utils OK")
    except Exception as e:
        print(f"[DIAG] nacl.utils FAIL: {e}")
# ─────────────────────────────────────────────────────────────────────────────

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
#       mode ("study"|"rest"),          ← runtime
#       phase_end_at (float ts),        ← runtime
#       remaining_on_pause (float|None) ← runtime
#     }
#   },
#   "breaks": [{ label, hhmm, duration_sec, _next_ts }],
#   "pause_until": float|None,          ← runtime
#   "last_channel_id": int|None,
#   "last_voice_channel_id": int|None,  ← runtime
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
            "timers":                {},
            "breaks":                [],
            "pause_until":           None,
            "last_channel_id":       None,
            "last_voice_channel_id": None,
            "voice_notice_sent":     False,
        }
        guild_locks[gid]  = asyncio.Lock()
        voice_queues[gid] = asyncio.Queue()
    return guild_states[gid]


def fmt_mm_ss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def state_exists(gs: dict) -> bool:
    """타이머 또는 쉬는시간이 1개 이상 있으면 True."""
    return bool(gs.get("timers")) or bool(gs.get("breaks"))


# ── Timer ops ─────────────────────────────────────────────────────────────────

def timer_pause(timer: dict) -> None:
    """남은 시간을 remaining_on_pause에 저장."""
    timer["remaining_on_pause"] = max(0.0, timer["phase_end_at"] - now_ts())


def timer_resume(timer: dict) -> None:
    """저장된 남은 시간으로 phase_end_at 재설정."""
    rem = timer.get("remaining_on_pause") or 0.0
    timer["phase_end_at"]       = now_ts() + rem
    timer["remaining_on_pause"] = None


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

                # 1) 쉬는시간 체크
                for brk in gs["breaks"]:
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
                        if ts >= t["phase_end_at"]:
                            overshoot = ts - t["phase_end_at"]
                            new_mode  = "rest" if t["mode"] == "study" else "study"
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

_RE_TIME = re.compile(r"^(\d+)(초|분|시간)(공부|휴식)$")
_RE_DUR  = re.compile(r"^(\d+)(초|분|시간)$")
_RE_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


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
    반환: 액션 리스트 (type: help | status | shutdown_all | break_end | stop | break | timer)

    우선순위:
      0) "도움말" / "help"               → help
      1) "상태"                          → status
      2) "종료"                          → shutdown_all
      3) "쉬는시간 끝"                   → break_end
      4) "[이름] 종료"                   → stop
      5) "쉬는시간 [라벨] HH:MM [N분]"  → break
      6) "[이름] [N분공부] [M분휴식]"    → timer
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

        # 3) 쉬는시간 강제 종료 ("쉬는시간 끝")
        if tok == "쉬는시간" and i + 1 < len(tokens) and tokens[i + 1] == "끝":
            actions.append({"type": "break_end"})
            i += 2
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

        # 6) [이름] [N분공부] [M분휴식]  (순서 무관)
        if i + 2 < len(tokens):
            r1 = _time_tok(tokens[i + 1])
            r2 = _time_tok(tokens[i + 2])
            if r1 and r2 and r1[0] != r2[0]:
                study = r1[1] if r1[0] == "study" else r2[1]
                rest  = r2[1] if r2[0] == "rest"  else r1[1]
                actions.append({
                    "type":      "timer",
                    "name":      tok,
                    "study_sec": study,
                    "rest_sec":  rest,
                })
                i += 3
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
            if gs["pause_until"] is not None and t.get("remaining_on_pause") is not None:
                rem   = t["remaining_on_pause"]
                end_s = "(일시정지)"
            else:
                rem   = t["phase_end_at"] - ts
                end_s = datetime.fromtimestamp(t["phase_end_at"], tz=KST).strftime("%H:%M:%S")
            lines.append(f"  • **{name}** [{ml}] 남은 시간 : {fmt_mm_ss(rem)} → {end_s}")
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
        "**2) 개인 타이머 종료**\n"
        "```\n"
        "--학교종 이름 종료\n"
        "--학교종 김동희 종료\n"
        "```\n"
        "\n"
        "**3) 전체 종료** (모든 타이머/쉬는시간 삭제 + 스케줄러 중지)\n"
        "```\n"
        "--학교종 종료\n"
        "```\n"
        "\n"
        "**4) 쉬는시간 등록**\n"
        "```\n"
        "--학교종 쉬는시간 점심시간 18:00 20분\n"
        "--학교종 쉬는시간 쉬는시간 14:30 10초\n"
        "--학교종 쉬는시간 점심시간 12:00 1시간\n"
        "```\n"
        "• HH:MM이 이미 지났으면 다음 날로 자동 예약됩니다.\n"
        "\n"
        "**5) 쉬는시간 강제 종료** (현재 일시정지 즉시 해제, 스케줄은 유지)\n"
        "```\n"
        "--학교종 쉬는시간 끝\n"
        "```\n"
        "\n"
        "**6) 상태 출력**\n"
        "```\n"
        "--학교종 상태\n"
        "```\n"
        "\n"
        "**7) 도움말**\n"
        "```\n"
        "--학교종 도움말\n"
        "--학교종 help\n"
        "```\n"
        "\n"
        "🔊 **음성 안내**\n"
        "• 명령을 보낸 사용자가 음성채널에 있으면 봇이 그 채널에 상주하며,\n"
        "  타이머 전환·쉬는시간마다 종소리(bell.mp3) + TTS로 안내합니다.\n"
        "• 음성 안내를 위해 프로젝트 루트에 bell.mp3 를 넣어주세요.\n"
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

        # 쉬는시간 복구
        for b in data.get("breaks", []):
            gs["breaks"].append({
                "label":        b["label"],
                "hhmm":         b["hhmm"],
                "duration_sec": b["duration_sec"],
                "_next_ts":     next_occurrence_ts(b["hhmm"]),
            })

        # 타이머 복구 — mode=study, now부터 리셋
        for name, td in data.get("timers", {}).items():
            gs["timers"][name] = {
                "study_sec":          td["study_sec"],
                "rest_sec":           td["rest_sec"],
                "channel_id":         td["channel_id"],
                "mode":               "study",
                "phase_end_at":       ts + td["study_sec"],
                "remaining_on_pause": None,
            }

        if gs["timers"] or gs["breaks"]:
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

    # 음성채널 추적: 명령 보낸 사용자가 음성채널에 있으면 저장
    if msg.guild and hasattr(msg.author, "voice") and msg.author.voice and msg.author.voice.channel:
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
                gs["pause_until"]       = None
                gs["voice_notice_sent"] = False
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

            # ── 개인 타이머 시작/재설정 ──
            elif atype == "timer":
                ts_now = now_ts()
                entry: dict = {
                    "study_sec":          act["study_sec"],
                    "rest_sec":           act["rest_sec"],
                    "channel_id":         cid,
                    "mode":               "study",
                    "phase_end_at":       ts_now + act["study_sec"],
                    "remaining_on_pause": None,
                }
                if gs["pause_until"] is not None:
                    timer_pause(entry)
                gs["timers"][act["name"]] = entry
                save_state()
                if gs["pause_until"] is not None:
                    replies.append(
                        f"✅ **{act['name']}** 타이머 등록 (현재 일시정지 중 — 재개 후 공부 시작) "
                        f"공부 {act['study_sec'] // 60}분 / 휴식 {act['rest_sec'] // 60}분"
                    )
                else:
                    edt = datetime.fromtimestamp(entry["phase_end_at"], tz=KST)
                    replies.append(
                        f"✅ **{act['name']}** 타이머 시작 "
                        f"— 공부 {act['study_sec'] // 60}분 / 휴식 {act['rest_sec'] // 60}분, "
                        f"첫 전환 {edt.strftime('%H:%M:%S')}"
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
