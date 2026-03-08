"""
학교종 Discord 봇 — 쉬는시간 서비스 (상태 변경 로직)
"""
from __future__ import annotations

from datetime import datetime

from app.config import KST
from app.domain.models import BreakEntry, GuildState
from app.repositories.state_repository import save_state
from app.services.guild_state_service import guild_states
from app.services.timer_service import timer_resume
from app.utils.time_utils import fmt_dur, next_occurrence_ts


def _save() -> None:
    save_state(guild_states)


# ── 일회성 쉬는시간 ──────────────────────────────────────────────────────────

def add_break(gs: GuildState, label: str, hhmm: str, duration_sec: int) -> str:
    """쉬는시간 등록. 항상 성공하며 reply 문자열을 반환."""
    brk = BreakEntry(
        label=label,
        hhmm=hhmm,
        duration_sec=duration_sec,
        next_ts=next_occurrence_ts(hhmm),
    )
    gs.breaks.append(brk)
    _save()
    ndt = datetime.fromtimestamp(brk.next_ts, tz=KST)
    return (
        f"✅ 쉬는시간 **{label}** 등록 "
        f"— {ndt.strftime('%m/%d %H:%M')} ({fmt_dur(duration_sec)})"
    )


def list_breaks(gs: GuildState) -> str:
    """쉬는시간 목록 조회."""
    if not gs.breaks:
        return "🔔 등록된 쉬는시간이 없습니다."
    lines = ["**🔔 쉬는시간 목록**"]
    for idx, b in enumerate(gs.breaks, 1):
        nts = b.next_ts or next_occurrence_ts(b.hhmm)
        ndt = datetime.fromtimestamp(nts, tz=KST)
        lines.append(
            f"  {idx}. **{b.label}** {b.hhmm} "
            f"({fmt_dur(b.duration_sec)}) "
            f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
        )
    return "\n".join(lines)


def delete_break(gs: GuildState, label: str) -> tuple[str, bool, bool]:
    """쉬는시간 삭제. Returns (reply, removed, state_empty)."""
    before = len(gs.breaks)
    gs.breaks = [b for b in gs.breaks if b.label != label]
    removed = before - len(gs.breaks)
    if removed:
        _save()
        return (
            f"✅ 쉬는시간 **{label}** 삭제 ({removed}건)",
            True,
            not gs.state_exists(),
        )
    return f"❌ **{label}** 쉬는시간을 찾을 수 없습니다.", False, False


# ── 쉬는시간 강제 종료 ───────────────────────────────────────────────────────

def break_end(gs: GuildState) -> tuple[str | None, bool]:
    """쉬는시간 강제 종료. Returns (reply_or_none, should_notify_resume)."""
    if gs.pause_until is None:
        return "ℹ️ 현재 일시정지 중이 아닙니다", False
    gs.pause_until = None
    for t in gs.timers.values():
        timer_resume(t)
    return None, True


# ── 정규쉬는시간 ─────────────────────────────────────────────────────────────

def add_recurring_break(
    gs: GuildState, label: str, hhmm: str, duration_sec: int,
) -> str:
    """정규쉬는시간 등록. 항상 성공하며 reply 문자열을 반환."""
    brk = BreakEntry(
        label=label,
        hhmm=hhmm,
        duration_sec=duration_sec,
        next_ts=next_occurrence_ts(hhmm),
    )
    gs.recurring_breaks.append(brk)
    _save()
    ndt = datetime.fromtimestamp(brk.next_ts, tz=KST)
    return (
        f"✅ 정규쉬는시간 **{label}** 등록 "
        f"— 매일 {hhmm} ({fmt_dur(duration_sec)}) "
        f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
    )


def list_recurring_breaks(gs: GuildState) -> str:
    """정규쉬는시간 목록 조회."""
    if not gs.recurring_breaks:
        return "🔁 등록된 정규쉬는시간이 없습니다."
    lines = ["**🔁 정규쉬는시간 목록**"]
    for idx, b in enumerate(gs.recurring_breaks, 1):
        nts = b.next_ts or next_occurrence_ts(b.hhmm)
        ndt = datetime.fromtimestamp(nts, tz=KST)
        lines.append(
            f"  {idx}. **{b.label}** 매일 {b.hhmm} "
            f"({fmt_dur(b.duration_sec)}) "
            f"→ 다음: {ndt.strftime('%m/%d %H:%M')}"
        )
    return "\n".join(lines)


def delete_recurring_break(gs: GuildState, label: str) -> tuple[str, bool, bool]:
    """정규쉬는시간 삭제. Returns (reply, removed, state_empty)."""
    before = len(gs.recurring_breaks)
    gs.recurring_breaks = [b for b in gs.recurring_breaks if b.label != label]
    removed = before - len(gs.recurring_breaks)
    if removed:
        _save()
        return (
            f"✅ 정규쉬는시간 **{label}** 삭제 ({removed}건)",
            True,
            not gs.state_exists(),
        )
    return f"❌ **{label}** 정규쉬는시간을 찾을 수 없습니다.", False, False
