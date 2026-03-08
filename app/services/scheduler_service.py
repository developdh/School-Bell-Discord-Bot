"""길드별 스케줄러 루프 — 타이머 전환, 쉬는시간 발동/종료, auto-stop, 통계 누적."""
from __future__ import annotations

import asyncio
import re

from app.config import log
from app.repositories.state_repository import save_state
from app.services import timer_service
from app.services.guild_state_service import (
    guild_locks,
    guild_states,
    guild_tasks,
    voice_queues,
)
from app.utils.time_utils import next_occurrence_ts, now_ts


def _save() -> None:
    save_state(guild_states)


async def guild_scheduler(gid: int) -> None:
    """길드별 0.5초 틱 이벤트 루프.

    쉬는시간 발동/종료, 타이머 페이즈 전환, auto-stop, 통계 누적,
    음성채널 연결 유지 등을 처리한다.
    """
    # 순환 임포트 방지: client.py → scheduler_service → client.py
    from app.bot.client import (
        _cancel_voice_worker,
        _ensure_voice_worker,
        _get_channel,
        ensure_voice_connected,
        ensure_voice_disconnected,
        notify_break_event,
        notify_resume,
        notify_transition,
        play_event_audio,
        update_status_panel,
    )

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
                                        timer_service.accumulate_stats(gs, _n, _t.mode, ts - _t.last_accounted_at)
                                    _t.last_accounted_at = ts
                            for t in gs.timers.values():
                                timer_service.timer_pause(t)
                        gs.pause_until = end_ts
                        await notify_break_event(gid, gs, brk, end_ts, already)
                        asyncio.create_task(update_status_panel(gid))
                    brk.next_ts = next_occurrence_ts(brk.hhmm)

                # 2) 일시정지 종료 체크
                if gs.pause_until is not None and ts >= gs.pause_until:
                    gs.pause_until = None
                    for t in gs.timers.values():
                        timer_service.timer_resume(t)
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
                            timer_service.accumulate_stats(gs, name, t.mode, ts - t.last_accounted_at)
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
    """길드 스케줄러 태스크가 없거나 종료되었으면 새로 생성."""
    t = guild_tasks.get(gid)
    if t is None or t.done():
        guild_tasks[gid] = asyncio.create_task(guild_scheduler(gid))


def cancel_scheduler(gid: int) -> None:
    """길드 스케줄러 태스크를 취소한다."""
    task = guild_tasks.pop(gid, None)
    if task and not task.done():
        task.cancel()
