"""
학교종 Discord 봇 — 시간 관련 유틸리티 (순수 함수)
"""
import re
from datetime import datetime, timedelta

from app.config import KST

# ── Regex patterns ────────────────────────────────────────────────────────────
_RE_TIME   = re.compile(r"^(\d+)(초|분|시간)(공부|휴식)$")
_RE_DUR    = re.compile(r"^(\d+)(초|분|시간)$")
_RE_HHMM   = re.compile(r"^\d{1,2}:\d{2}$")
_RE_REPEAT = re.compile(r"^(\d+)회반복$")


# ── Time helpers ──────────────────────────────────────────────────────────────

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


def fmt_mm_ss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def fmt_dur(sec: int) -> str:
    """초 → 사람이 읽기 좋은 문자열 (예: '30초', '10분', '1시간 30분')"""
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    parts: list[str] = []
    if h: parts.append(f"{h}시간")
    if m: parts.append(f"{m}분")
    if s: parts.append(f"{s}초")
    return " ".join(parts) if parts else "0초"


# ── Token parsers ─────────────────────────────────────────────────────────────

def unit_to_sec(n: int, unit: str) -> int:
    if unit == "초":   return n
    if unit == "시간": return n * 3600
    return n * 60  # 분


def time_tok(s: str) -> tuple[str, int] | None:
    """'10분공부' → ('study', 600), '30초휴식' → ('rest', 30)"""
    m = _RE_TIME.match(s)
    if not m:
        return None
    return ("study" if m.group(3) == "공부" else "rest"), unit_to_sec(int(m.group(1)), m.group(2))


def dur_tok(s: str) -> int | None:
    """'20분' → 1200, '30초' → 30, '1시간' → 3600"""
    m = _RE_DUR.match(s)
    return unit_to_sec(int(m.group(1)), m.group(2)) if m else None
