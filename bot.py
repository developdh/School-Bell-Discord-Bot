"""
학교종 Discord 봇
prefix: --학교종
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

KST        = ZoneInfo("Asia/Seoul")
STATE_FILE = Path("state.json")
PREFIX     = "--학교종"
TTS_CACHE  = Path("./tts_cache")

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
# }
guild_states: dict[int, dict]         = {}
guild_locks:  dict[int, asyncio.Lock] = {}
guild_tasks:  dict[int, asyncio.Task] = {}
voice_locks:  dict[int, asyncio.Lock] = {}


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
        }
        guild_locks[gid] = asyncio.Lock()
        voice_locks[gid] = asyncio.Lock()
    return guild_states[gid]


def fmt_mm_ss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# ── Timer ops ─────────────────────────────────────────────────────────────────

def timer_pause(timer: dict) -> None:
    """남은 시간을 remaining_on_pause에 저장."""
    timer["remaining_on_pause"] = max(0.0, timer["phase_end_at"] - now_ts())


def timer_resume(timer: dict) -> None:
    """저장된 남은 시간으로 phase_end_at 재설정."""
    rem = timer.get("remaining_on_pause") or 0.0
    timer["phase_end_at"]       = now_ts() + rem
    timer["remaining_on_pause"] = None


# ── TTS / Voice ───────────────────────────────────────────────────────────────

def _tts_cache_path(sentence: str) -> Path:
    safe = re.sub(r"[^\w가-힣]", "_", sentence)[:60]
    return TTS_CACHE / f"{safe}.mp3"


async def _make_tts(sentence: str, path: Path) -> bool:
    """TTS 파일 생성. 성공 시 True 반환."""
    # edge-tts 우선 시도
    try:
        import edge_tts
        comm = edge_tts.Communicate(sentence, voice="ko-KR-SunHiNeural")
        await comm.save(str(path))
        return True
    except Exception as e:
        log.warning("edge-tts 실패, gTTS 시도: %s", e)

    # gTTS fallback
    try:
        from gtts import gTTS
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: gTTS(text=sentence, lang="ko").save(str(path)))
        return True
    except Exception as e:
        log.warning("gTTS 실패: %s", e)

    return False


async def _get_tts_path(sentence: str) -> Path | None:
    """TTS 파일 경로 반환 (없으면 생성)."""
    TTS_CACHE.mkdir(exist_ok=True)
    path = _tts_cache_path(sentence)
    if path.exists():
        return path
    ok = await _make_tts(sentence, path)
    return path if ok else None


async def _play_voice_audio(
    vc: discord.VoiceClient,
    loop: asyncio.AbstractEventLoop,
    path: Path,
) -> None:
    """단일 파일을 재생하고 완료까지 대기."""
    done = asyncio.Event()

    def after(_: Exception | None) -> None:
        loop.call_soon_threadsafe(done.set)

    try:
        vc.play(discord.FFmpegPCMAudio(str(path)), after=after)
    except Exception as e:
        log.warning("재생 시작 실패 %s: %s", path, e)
        return
    await done.wait()


async def _play_voice(gid: int, sentence: str) -> None:
    """벨 + TTS를 음성채널에서 순차 재생. 실패 시 로그만 남기고 스킵."""
    gs = guild_states.get(gid, {})
    if not gs.get("timers"):
        return

    vc_id = gs.get("last_voice_channel_id")
    if not vc_id:
        return

    vc_channel = bot.get_channel(vc_id)
    if not isinstance(vc_channel, discord.VoiceChannel):
        return

    tts_path = await _get_tts_path(sentence)

    vlock = voice_locks.get(gid)
    if vlock is None:
        return

    async with vlock:
        voice_client: discord.VoiceClient | None = None
        try:
            loop = asyncio.get_running_loop()

            # 이미 연결된 VoiceClient 확인
            existing = discord.utils.get(bot.voice_clients, guild=vc_channel.guild)
            if existing and existing.is_connected():
                if existing.channel.id != vc_id:
                    await existing.move_to(vc_channel)
                voice_client = existing  # type: ignore[assignment]
            else:
                voice_client = await vc_channel.connect(timeout=10.0)

            # 1) bell.mp3 / bell.wav 재생
            for bell in (Path("bell.mp3"), Path("bell.wav")):
                if bell.exists():
                    await _play_voice_audio(voice_client, loop, bell)
                    break

            # 2) TTS 재생
            if tts_path and tts_path.exists():
                await _play_voice_audio(voice_client, loop, tts_path)

        except Exception:
            log.exception("음성 재생 실패 guild=%d", gid)
        finally:
            if voice_client and voice_client.is_connected():
                await voice_client.disconnect()


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


async def notify_transition(gid: int, cid: int, name: str, mode: str) -> None:
    ch = await _get_channel(cid)
    if ch:
        label = "휴식" if mode == "rest" else "공부"
        await ch.send(f"🔔 학교종! **{name}** {label}")
    sentence = f"{name} {'공부' if mode == 'study' else '휴식'} 시작"
    asyncio.create_task(_play_voice(gid, sentence))


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
            f"{brk['duration_sec'] // 60}분 일시정지 "
            f"(→ {end_dt.strftime('%H:%M:%S')} 재개)"
        )
    for ch in await _break_channels(gs):
        await ch.send(msg)
    if not extending:
        sentence = f"{brk['label']} 쉬는시간 시작. 모두 일시정지"
        asyncio.create_task(_play_voice(gid, sentence))


async def notify_resume(gid: int, gs: dict) -> None:
    for ch in await _break_channels(gs):
        await ch.send("▶️ 쉬는시간 종료! 모든 타이머 재개")
    asyncio.create_task(_play_voice(gid, "쉬는시간 종료. 모두 재개"))


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
                    # 이 쉬는시간이 지금 발동
                    end_ts   = ts + brk["duration_sec"]
                    already  = gs["pause_until"] is not None
                    if not already or gs["pause_until"] < end_ts:
                        if not already:
                            # 처음 일시정지: 모든 타이머 남은 시간 저장
                            for t in gs["timers"].values():
                                timer_pause(t)
                        gs["pause_until"] = end_ts
                        await notify_break_event(gid, gs, brk, end_ts, already)
                    # 다음 날 스케줄로 갱신
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
                            t["mode"]          = new_mode
                            t["phase_end_at"]  = ts + t[f"{new_mode}_sec"] - overshoot
                            await notify_transition(gid, t["channel_id"], name, new_mode)

    except asyncio.CancelledError:
        log.info("스케줄러 종료 guild=%d", gid)
    except Exception:
        log.exception("스케줄러 예외 guild=%d", gid)


def ensure_scheduler(gid: int) -> None:
    t = guild_tasks.get(gid)
    if t is None or t.done():
        guild_tasks[gid] = asyncio.create_task(guild_scheduler(gid))


# ── Parser ────────────────────────────────────────────────────────────────────

_RE_TIME = re.compile(r"^(\d+)분(공부|휴식)$")
_RE_DUR  = re.compile(r"^(\d+)분$")
_RE_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


def _time_tok(s: str) -> tuple[str, int] | None:
    """'10분공부' → ('study', 600), '5분휴식' → ('rest', 300)"""
    m = _RE_TIME.match(s)
    if not m:
        return None
    return ("study" if m.group(2) == "공부" else "rest"), int(m.group(1)) * 60


def _dur_tok(s: str) -> int | None:
    """'20분' → 1200"""
    m = _RE_DUR.match(s)
    return int(m.group(1)) * 60 if m else None


def parse_command(raw: str) -> list[dict]:
    """
    공백 토큰 기반 왼쪽부터 순차 파싱.
    반환: 액션 리스트 (type: status | shutdown_all | break_end | stop | break | timer)

    우선순위:
      1) "상태"
      2) "종료"                         → shutdown_all
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
                f"({b['duration_sec'] // 60}분)"
            )
    else:
        lines.append("🔔 등록된 쉬는시간 없음")

    return "\n".join(lines)


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

            # ── 상태 ──
            if atype == "status":
                replies.append(build_status(gs))

            # ── 전체 종료 ──
            elif atype == "shutdown_all":
                gs["timers"].clear()
                gs["breaks"].clear()
                gs["pause_until"] = None
                save_state()
                task = guild_tasks.pop(gid, None)
                if task:
                    task.cancel()
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
                    replies.append("▶️ 쉬는시간 강제 종료: 모든 타이머 재개")

            # ── 종료 ──
            elif atype == "stop":
                name = act["name"]
                if name in gs["timers"]:
                    del gs["timers"][name]
                    save_state()
                    replies.append(f"✅ **{name}** 타이머 종료")
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
                    f"— {ndt.strftime('%m/%d %H:%M')} ({act['duration_sec'] // 60}분)"
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
                # 현재 pause 중이면 이 타이머도 즉시 pause 상태로
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

        await send_split(msg.channel, "\n".join(replies))


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN 환경변수를 설정하세요.")
    bot.run(token, log_handler=None)
