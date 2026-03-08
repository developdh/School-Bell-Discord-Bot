"""길드별 전역 상태, 락, 태스크 레지스트리."""
from __future__ import annotations

import asyncio

from app.domain.models import GuildState

# ── Per-guild registries ───────────────────────────────────────────────────────
guild_states:  dict[int, GuildState]    = {}
guild_locks:   dict[int, asyncio.Lock]  = {}
guild_tasks:   dict[int, asyncio.Task]  = {}
voice_queues:  dict[int, asyncio.Queue] = {}
voice_workers: dict[int, asyncio.Task]  = {}
panel_tasks:   dict[int, asyncio.Task]  = {}


def get_guild_state(gid: int) -> GuildState:
    """gid에 해당하는 GuildState를 반환. 없으면 생성."""
    if gid not in guild_states:
        guild_states[gid] = GuildState()
        guild_locks[gid]  = asyncio.Lock()
        voice_queues[gid] = asyncio.Queue()
    return guild_states[gid]
