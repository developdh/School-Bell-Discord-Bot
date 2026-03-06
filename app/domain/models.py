"""
학교종 Discord 봇 — 도메인 모델 (dataclass 기반)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.utils.time_utils import next_occurrence_ts


@dataclass
class Timer:
    study_sec: int
    rest_sec: int
    channel_id: int
    mode: str = "study"                                    # "study" | "rest"
    phase_end_at: float = 0.0
    remaining_on_pause: float | None = None
    remaining_on_personal_pause: float | None = None
    auto_stop_cycles: int | None = None
    cycle_count: int = 0
    auto_stop_ts: float | None = None
    last_accounted_at: float = 0.0

    def to_save_dict(self) -> dict:
        """영속 저장용 (runtime 필드 제외)."""
        return {
            "study_sec":  self.study_sec,
            "rest_sec":   self.rest_sec,
            "channel_id": self.channel_id,
        }

    @classmethod
    def from_saved(cls, data: dict, ts: float) -> Timer:
        """state.json에서 복원 — study 모드, now부터 리셋."""
        return cls(
            study_sec=data["study_sec"],
            rest_sec=data["rest_sec"],
            channel_id=data["channel_id"],
            mode="study",
            phase_end_at=ts + data["study_sec"],
            last_accounted_at=ts,
        )


@dataclass
class BreakEntry:
    label: str
    hhmm: str
    duration_sec: int
    next_ts: float = 0.0

    def to_save_dict(self) -> dict:
        """영속 저장용 (_next_ts 제외)."""
        return {
            "label":        self.label,
            "hhmm":         self.hhmm,
            "duration_sec": self.duration_sec,
        }

    @classmethod
    def from_saved(cls, data: dict) -> BreakEntry:
        """state.json에서 복원 — next_ts 재계산."""
        return cls(
            label=data["label"],
            hhmm=data["hhmm"],
            duration_sec=data["duration_sec"],
            next_ts=next_occurrence_ts(data["hhmm"]),
        )


@dataclass
class GuildState:
    timers: dict[str, Timer] = field(default_factory=dict)
    presets: dict[str, str] = field(default_factory=dict)
    breaks: list[BreakEntry] = field(default_factory=list)
    recurring_breaks: list[BreakEntry] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    pause_until: float | None = None
    last_channel_id: int | None = None
    last_voice_channel_id: int | None = None
    pinned_voice_channel_id: int | None = None
    voice_notice_sent: bool = False
    status_panel_channel_id: int | None = None
    status_panel_message_id: int | None = None
    last_stats_save: float = 0.0                           # runtime only

    def state_exists(self) -> bool:
        """타이머 또는 쉬는시간이 1개 이상 있으면 True."""
        return bool(self.timers) or bool(self.breaks) or bool(self.recurring_breaks)

    def to_save_dict(self) -> dict:
        """state.json 저장용 dict."""
        return {
            "last_channel_id":          self.last_channel_id,
            "pinned_voice_channel_id":  self.pinned_voice_channel_id,
            "presets":                  self.presets,
            "timers": {
                name: t.to_save_dict()
                for name, t in self.timers.items()
            },
            "breaks": [b.to_save_dict() for b in self.breaks],
            "recurring_breaks": [b.to_save_dict() for b in self.recurring_breaks],
            "stats":                    self.stats,
            "status_panel_channel_id":  self.status_panel_channel_id,
            "status_panel_message_id":  self.status_panel_message_id,
        }
