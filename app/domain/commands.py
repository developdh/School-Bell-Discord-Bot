"""명령 표현 — 파서가 반환하는 dataclass 명령 객체들."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class HelpCommand: pass

@dataclass
class StatusCommand: pass

@dataclass
class StatsCommand:
    name: str | None = None

@dataclass
class AttendanceCommand: pass

@dataclass
class OpenPanelCommand: pass

@dataclass
class ClosePanelCommand: pass

@dataclass
class RefreshPanelCommand: pass

@dataclass
class ShutdownAllCommand: pass

@dataclass
class VoicePinCommand: pass

@dataclass
class VoiceUnpinCommand: pass

@dataclass
class PresetSaveCommand:
    name: str
    content: str

@dataclass
class PresetRunCommand:
    name: str

@dataclass
class PresetListCommand: pass

@dataclass
class PresetDeleteCommand:
    name: str

@dataclass
class BreakEndCommand: pass

@dataclass
class BreakListCommand: pass

@dataclass
class BreakDeleteCommand:
    label: str

@dataclass
class AddBreakCommand:
    label: str
    hhmm: str
    duration_sec: int

@dataclass
class RecurringBreakAddCommand:
    label: str
    hhmm: str
    duration_sec: int

@dataclass
class RecurringBreakListCommand: pass

@dataclass
class RecurringBreakDeleteCommand:
    label: str

@dataclass
class PersonalPauseCommand:
    name: str

@dataclass
class PersonalResumeCommand:
    name: str

@dataclass
class SetRemainingCommand:
    name: str
    seconds: int

@dataclass
class StopTimerCommand:
    name: str

@dataclass
class SetTimerCommand:
    name: str
    study_sec: int
    rest_sec: int
    auto_stop_cycles: int | None = None
    auto_stop_hhmm: str | None = None
