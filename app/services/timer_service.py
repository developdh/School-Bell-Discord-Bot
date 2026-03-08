"""
학교종 Discord 봇 — 타이머 서비스 (상태 변경 로직)
"""
from __future__ import annotations

from datetime import datetime

from app.config import KST
from app.domain.models import GuildState, Timer
from app.repositories.state_repository import save_state
from app.services.guild_state_service import guild_states
from app.utils.time_utils import fmt_dur, fmt_mm_ss, now_ts


def _save() -> None:
    save_state(guild_states)


# ── 순수 타이머 상태 조작 ─────────────────────────────────────────────────────

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


def accumulate_stats(gs: GuildState, name: str, mode: str, seconds: float) -> None:
    if seconds <= 0:
        return
    date_key = datetime.now(KST).strftime("%Y-%m-%d")
    day = gs.stats.setdefault(date_key, {})
    entry = day.setdefault(name, {"study": 0.0, "rest": 0.0})
    entry["study" if mode == "study" else "rest"] += seconds


# ── 커맨드 핸들러용 서비스 함수 ───────────────────────────────────────────────

def set_timer(
    gs: GuildState, name: str, study_sec: int, rest_sec: int,
    channel_id: int, auto_stop_cycles: int | None, auto_stop_hhmm: str | None,
) -> str:
    """타이머 생성/재설정. 항상 성공하며 reply 문자열을 반환."""
    ts_now = now_ts()
    _as_ts = None
    if auto_stop_hhmm:
        _now = datetime.now(KST)
        _h, _m = map(int, auto_stop_hhmm.split(":"))
        _as_ts = _now.replace(hour=_h, minute=_m, second=0, microsecond=0).timestamp()
    entry = Timer(
        study_sec=study_sec,
        rest_sec=rest_sec,
        channel_id=channel_id,
        mode="study",
        phase_end_at=ts_now + study_sec,
        auto_stop_cycles=auto_stop_cycles,
        auto_stop_ts=_as_ts,
        last_accounted_at=ts_now,
    )
    if gs.pause_until is not None:
        timer_pause(entry)
    gs.timers[name] = entry
    _save()
    suffix = ""
    if auto_stop_cycles:
        suffix += f" | {auto_stop_cycles}회 반복"
    if auto_stop_hhmm:
        suffix += f" | 오늘 {auto_stop_hhmm} 종료"
    if gs.pause_until is not None:
        return (
            f"✅ **{name}** 타이머 등록 (현재 일시정지 중 — 재개 후 공부 시작) "
            f"공부 {study_sec // 60}분 / 휴식 {rest_sec // 60}분"
            + suffix
        )
    else:
        edt = datetime.fromtimestamp(entry.phase_end_at, tz=KST)
        return (
            f"✅ **{name}** 타이머 시작 "
            f"— 공부 {study_sec // 60}분 / 휴식 {rest_sec // 60}분, "
            f"첫 전환 {edt.strftime('%H:%M:%S')}"
            + suffix
        )


def stop_timer(gs: GuildState, name: str) -> tuple[str, bool, bool]:
    """타이머 종료. Returns (reply, removed, state_empty)."""
    if name not in gs.timers:
        return f"❌ **{name}** 타이머 없음", False, False
    t = gs.timers[name]
    if gs.pause_until is None and t.remaining_on_personal_pause is None:
        if t.last_accounted_at > 0:
            accumulate_stats(gs, name, t.mode, now_ts() - t.last_accounted_at)
    del gs.timers[name]
    _save()
    return f"✅ **{name}** 타이머 종료", True, not gs.state_exists()


def shutdown_all(gs: GuildState) -> str:
    """전체 종료: 모든 타이머/쉬는시간 삭제. 항상 성공."""
    _ts = now_ts()
    if gs.pause_until is None:
        for _n, _t in gs.timers.items():
            if _t.remaining_on_personal_pause is None:
                if _t.last_accounted_at > 0 and _t.last_accounted_at < _ts:
                    accumulate_stats(gs, _n, _t.mode, _ts - _t.last_accounted_at)
    gs.timers.clear()
    gs.breaks.clear()
    gs.recurring_breaks.clear()
    gs.pause_until              = None
    gs.pinned_voice_channel_id  = None
    gs.last_voice_channel_id    = None
    gs.voice_notice_sent        = False
    _save()
    return "✅ 전체 종료: 모든 타이머/쉬는시간 중지"


def do_personal_pause(gs: GuildState, name: str) -> tuple[str, bool]:
    """개인 일시정지. Returns (reply, changed)."""
    t = gs.timers.get(name)
    if t is None:
        return f"❌ **{name}** 타이머 없음", False
    if t.remaining_on_personal_pause is not None:
        return f"ℹ️ **{name}** 이미 일시정지 중입니다.", False
    if gs.pause_until is None:
        if t.last_accounted_at > 0:
            accumulate_stats(gs, name, t.mode, now_ts() - t.last_accounted_at)
        t.last_accounted_at = now_ts()
    timer_personal_pause(t, gs)
    return (
        f"⏸️ **{name}** 일시정지 "
        f"(남은 시간 {fmt_mm_ss(t.remaining_on_personal_pause)} 저장)"
    ), True


def do_personal_resume(gs: GuildState, name: str) -> tuple[str, bool]:
    """개인 재개. Returns (reply, changed)."""
    t = gs.timers.get(name)
    if t is None:
        return f"❌ **{name}** 타이머 없음", False
    if t.remaining_on_personal_pause is None:
        return f"ℹ️ **{name}** 일시정지 상태가 아닙니다.", False
    timer_personal_resume(t, gs)
    t.last_accounted_at = now_ts()
    if gs.pause_until is not None:
        return (
            f"▶️ **{name}** 개인 일시정지 해제 "
            f"(전체 쉬는시간 종료 후 재개됩니다)"
        ), True
    else:
        edt = datetime.fromtimestamp(t.phase_end_at, tz=KST)
        return f"▶️ **{name}** 재개 → {edt.strftime('%H:%M:%S')}", True


def do_set_remaining(gs: GuildState, name: str, new_sec: int) -> tuple[str, bool]:
    """남은시간 수정. Returns (reply, changed)."""
    t = gs.timers.get(name)
    if t is None:
        return f"❌ **{name}** 타이머 없음", False
    if t.remaining_on_personal_pause is not None:
        t.remaining_on_personal_pause = float(new_sec)
        return f"✅ **{name}** 남은시간 → {fmt_dur(new_sec)} (개인 일시정지 중)", True
    elif t.remaining_on_pause is not None:
        t.remaining_on_pause = float(new_sec)
        return f"✅ **{name}** 남은시간 → {fmt_dur(new_sec)} (전체 일시정지 중)", True
    else:
        t.phase_end_at = now_ts() + new_sec
        edt = datetime.fromtimestamp(t.phase_end_at, tz=KST)
        return (
            f"✅ **{name}** 남은시간 → {fmt_dur(new_sec)} "
            f"(전환 {edt.strftime('%H:%M:%S')})"
        ), True
