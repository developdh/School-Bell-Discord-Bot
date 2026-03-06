"""공백 토큰 기반 명령 파서 — 반환값: command dataclass list."""
from __future__ import annotations

from app.domain.commands import (
    AddBreakCommand, AttendanceCommand, BreakDeleteCommand,
    BreakEndCommand, BreakListCommand, ClosePanelCommand,
    HelpCommand, OpenPanelCommand, PersonalPauseCommand,
    PersonalResumeCommand, PresetDeleteCommand, PresetListCommand,
    PresetRunCommand, PresetSaveCommand, RecurringBreakAddCommand,
    RecurringBreakDeleteCommand, RecurringBreakListCommand,
    RefreshPanelCommand, SetRemainingCommand, SetTimerCommand,
    ShutdownAllCommand, StatusCommand, StatsCommand, StopTimerCommand,
    VoicePinCommand, VoiceUnpinCommand,
)
from app.utils.time_utils import _RE_HHMM, _RE_REPEAT, dur_tok, time_tok


def parse_command(raw: str) -> list:
    """공백 토큰 기반 왼쪽부터 순차 파싱. 반환: command dataclass list."""
    tokens = raw.strip().split()
    commands: list = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # 0) 도움말 / help
        if tok == "도움말" or tok.lower() == "help":
            commands.append(HelpCommand())
            i += 1
            continue

        # 1) 상태
        if tok == "상태":
            commands.append(StatusCommand())
            i += 1
            continue

        # 1-1) 통계 / 통계 [이름]
        if tok == "통계":
            if i + 1 < len(tokens):
                commands.append(StatsCommand(name=tokens[i + 1]))
                i += 2
            else:
                commands.append(StatsCommand())
                i += 1
            continue

        # 1-2) 출석
        if tok == "출석":
            commands.append(AttendanceCommand())
            i += 1
            continue

        # 1-3) 패널 / 패널 해제 / 패널 새로고침
        if tok == "패널":
            if i + 1 < len(tokens) and tokens[i + 1] == "해제":
                commands.append(ClosePanelCommand())
                i += 2
                continue
            if i + 1 < len(tokens) and tokens[i + 1] == "새로고침":
                commands.append(RefreshPanelCommand())
                i += 2
                continue
            commands.append(OpenPanelCommand())
            i += 1
            continue

        # 2) 전체 종료
        if tok == "종료":
            commands.append(ShutdownAllCommand())
            i += 1
            continue

        # 2a) 음성채널 고정 / 해제
        if tok == "음성채널" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub == "고정":
                commands.append(VoicePinCommand())
                i += 2
                continue
            if sub == "해제":
                commands.append(VoiceUnpinCommand())
                i += 2
                continue

        # 2c-f) 프리셋
        if tok == "프리셋" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub == "저장" and i + 3 < len(tokens):
                pname = tokens[i + 2]
                content = " ".join(tokens[i + 3:])
                commands.append(PresetSaveCommand(name=pname, content=content))
                i = len(tokens)
                continue
            if sub == "실행" and i + 2 < len(tokens):
                commands.append(PresetRunCommand(name=tokens[i + 2]))
                i += 3
                continue
            if sub == "목록":
                commands.append(PresetListCommand())
                i += 2
                continue
            if sub == "삭제" and i + 2 < len(tokens):
                commands.append(PresetDeleteCommand(name=tokens[i + 2]))
                i += 3
                continue

        # 3) 쉬는시간 강제 종료
        if tok == "쉬는시간" and i + 1 < len(tokens) and tokens[i + 1] == "끝":
            commands.append(BreakEndCommand())
            i += 2
            continue

        # 3a) 쉬는시간 목록
        if tok == "쉬는시간" and i + 1 < len(tokens) and tokens[i + 1] == "목록":
            commands.append(BreakListCommand())
            i += 2
            continue

        # 3b) 쉬는시간 삭제
        if tok == "쉬는시간" and i + 2 < len(tokens) and tokens[i + 1] == "삭제":
            commands.append(BreakDeleteCommand(label=tokens[i + 2]))
            i += 3
            continue

        # 3b-2) 정규쉬는시간 추가/목록/삭제
        if tok == "정규쉬는시간" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub == "추가" and i + 4 < len(tokens):
                label, hhmm, dur_s = tokens[i + 2], tokens[i + 3], tokens[i + 4]
                if _RE_HHMM.match(hhmm):
                    dur = dur_tok(dur_s)
                    if dur is not None:
                        commands.append(RecurringBreakAddCommand(
                            label=label, hhmm=hhmm, duration_sec=dur,
                        ))
                        i += 5
                        continue
            if sub == "목록":
                commands.append(RecurringBreakListCommand())
                i += 2
                continue
            if sub == "삭제" and i + 2 < len(tokens):
                commands.append(RecurringBreakDeleteCommand(label=tokens[i + 2]))
                i += 3
                continue

        # 3c) 일시정지 [이름]
        if tok == "일시정지" and i + 1 < len(tokens):
            commands.append(PersonalPauseCommand(name=tokens[i + 1]))
            i += 2
            continue

        # 3d) 재개 [이름]
        if tok == "재개" and i + 1 < len(tokens):
            commands.append(PersonalResumeCommand(name=tokens[i + 1]))
            i += 2
            continue

        # 3e) 남은시간 [이름] [N분]
        if tok == "남은시간" and i + 2 < len(tokens):
            dur = dur_tok(tokens[i + 2])
            if dur is not None:
                commands.append(SetRemainingCommand(name=tokens[i + 1], seconds=dur))
                i += 3
                continue

        # 4) [이름] 종료
        if i + 1 < len(tokens) and tokens[i + 1] == "종료":
            commands.append(StopTimerCommand(name=tok))
            i += 2
            continue

        # 5) 쉬는시간 [라벨] HH:MM [N분]
        if tok == "쉬는시간" and i + 3 < len(tokens):
            label, hhmm, dur_s = tokens[i + 1], tokens[i + 2], tokens[i + 3]
            if _RE_HHMM.match(hhmm):
                dur = dur_tok(dur_s)
                if dur is not None:
                    commands.append(AddBreakCommand(
                        label=label, hhmm=hhmm, duration_sec=dur,
                    ))
                    i += 4
                    continue

        # 6) [이름] [N분공부] [M분휴식] [N회반복]? [오늘끝 HH:MM]?
        if i + 2 < len(tokens):
            r1 = time_tok(tokens[i + 1])
            r2 = time_tok(tokens[i + 2])
            if r1 and r2 and r1[0] != r2[0]:
                study = r1[1] if r1[0] == "study" else r2[1]
                rest  = r2[1] if r2[0] == "rest"  else r1[1]
                i += 3
                auto_cycles: int | None   = None
                auto_end_hhmm: str | None = None
                while i < len(tokens):
                    rm = _RE_REPEAT.match(tokens[i])
                    if rm:
                        auto_cycles = int(rm.group(1))
                        i += 1
                        continue
                    if tokens[i] == "오늘끝" and i + 1 < len(tokens) and _RE_HHMM.match(tokens[i + 1]):
                        auto_end_hhmm = tokens[i + 1]
                        i += 2
                        continue
                    break
                commands.append(SetTimerCommand(
                    name=tok,
                    study_sec=study,
                    rest_sec=rest,
                    auto_stop_cycles=auto_cycles,
                    auto_stop_hhmm=auto_end_hhmm,
                ))
                continue

        i += 1  # 인식 불가 토큰 → 건너뜀

    return commands
