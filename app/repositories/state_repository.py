"""
학교종 Discord 봇 — JSON 영속성
"""
from __future__ import annotations

import json

from app.config import STATE_FILE, log
from app.domain.models import GuildState


def load_state() -> dict:
    """state.json에서 raw dict 로드. 파일 없거나 에러 시 빈 dict 반환."""
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("state.json 로드 실패: %s", e)
        return {}


def save_state(guild_states: dict[int, GuildState]) -> None:
    """guild_states를 state.json에 저장."""
    data: dict = {}
    for gid, gs in guild_states.items():
        data[str(gid)] = gs.to_save_dict()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
