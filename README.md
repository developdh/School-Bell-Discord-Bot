# 학교종 Discord 봇

공부/휴식 타이머와 쉬는시간 알림을 음성채널 종소리 + TTS로 알려주는 Discord 봇입니다.

## 주요 기능

- **개인 타이머** — 이름별로 공부·휴식 사이클을 무한 반복하며, 전환 시 텍스트 + 음성으로 안내
- **자동 종료 조건** — N회 반복 후 자동 종료, 특정 시각에 자동 종료 (둘 다 동시 설정 가능)
- **쉬는시간 (일회성)** — 지정 시각에 모든 타이머를 일시정지하고, 종료 시 자동 재개
- **정규쉬는시간 (매일 반복)** — 매일 같은 시각에 자동 발동하는 쉬는시간
- **개인 일시정지/재개** — 전체 쉬는시간과 별개로 개인 타이머만 정지/재개
- **남은시간 수정** — 현재 페이즈(공부/휴식)의 남은 시간을 임의 변경
- **음성 안내** — 음성채널 자동 접속 후 종소리(bell.mp3) + TTS(edge-tts/gTTS) 재생
- **음성채널 고정** — 봇 재시작 후에도 지정 채널에 자동 접속
- **상태 패널** — Discord Embed로 타이머·쉬는시간·통계를 10초마다 자동 갱신
- **통계** — 일별·개인별 공부/휴식 시간 자동 집계 (최근 7일)
- **출석** — 하루 공부 60분 이상이면 출석 체크
- **프리셋** — 자주 쓰는 명령을 이름으로 저장해두고 한 번에 실행
- **다중 타이머** — 한 명령으로 여러 사람의 타이머를 동시에 등록
- **시간 단위** — 초 / 분 / 시간 모두 입력 가능, 순서 무관
- **영속성** — 타이머·쉬는시간·통계·프리셋 등 모든 상태가 `state.json`에 저장되어 봇 재시작 후 자동 복구
- **다중 서버** — 서버(길드)별 완전 독립 운영

---

## 명령어

prefix: `--학교종`

### 1. 개인 타이머 시작/재설정

```
--학교종 이름 10분공부 5분휴식
--학교종 이름 30초공부 10초휴식
--학교종 이름 1시간공부 10분휴식
--학교종 김동희 10분공부 5분휴식 서채영 1시간공부 20분휴식
```

- 시간 단위: `초` / `분` / `시간` 모두 가능
- 공부/휴식 순서 무관
- 이미 등록된 이름이면 타이머가 재설정됨
- 한 명령으로 복수 등록 가능

### 2. 자동 종료 조건 (선택)

```
--학교종 김동희 10분공부 5분휴식 4회반복
--학교종 김동희 10분공부 5분휴식 오늘끝 18:00
--학교종 김동희 10분공부 5분휴식 4회반복 오늘끝 18:00
```

| 옵션 | 설명 |
|------|------|
| `N회반복` | 공부→휴식을 N번 반복 후 자동 종료 |
| `오늘끝 HH:MM` | 해당 시각에 자동 종료 |

두 조건을 동시에 설정하면, 먼저 도달하는 조건에서 종료됩니다.
재시작 시 진행도(사이클 카운트)는 리셋됩니다.

### 3. 개인 타이머 종료

```
--학교종 이름 종료
--학교종 김동희 종료
```

### 4. 개인 일시정지 / 재개

```
--학교종 일시정지 김동희
--학교종 재개 김동희
```

- 전체 쉬는시간과 별개로 개인 타이머만 정지/재개
- 전체 쉬는시간 중에도 개인 일시정지 상태는 유지됨

### 5. 남은시간 수정

```
--학교종 남은시간 김동희 10분
--학교종 남은시간 김동희 30초
```

현재 페이즈(공부/휴식)의 남은 시간을 변경합니다.

### 6. 전체 종료

```
--학교종 종료
```

모든 타이머·쉬는시간을 삭제하고 스케줄러를 중지합니다. 봇은 대기 상태로 전환됩니다.

### 7. 쉬는시간 (일회성)

```
--학교종 쉬는시간 점심시간 12:00 1시간
--학교종 쉬는시간 쉬는시간 14:30 10분
--학교종 쉬는시간 목록
--학교종 쉬는시간 삭제 점심시간
--학교종 쉬는시간 끝
```

| 명령 | 설명 |
|------|------|
| `쉬는시간 라벨 HH:MM 기간` | 지정 시각에 모든 타이머를 일시정지 |
| `쉬는시간 목록` | 등록된 쉬는시간 목록 |
| `쉬는시간 삭제 라벨` | 특정 쉬는시간 삭제 |
| `쉬는시간 끝` | 현재 진행 중인 쉬는시간(일시정지)을 즉시 종료 (스케줄은 유지) |

- HH:MM이 이미 지났으면 다음 날로 자동 예약
- 쉬는시간이 시작되면 모든 타이머의 남은 시간을 보존하고 일시정지
- 쉬는시간 종료 시 보존된 시간으로 자동 재개

### 8. 정규쉬는시간 (매일 반복)

```
--학교종 정규쉬는시간 추가 점심 12:00 1시간
--학교종 정규쉬는시간 목록
--학교종 정규쉬는시간 삭제 점심
```

- 매일 같은 시각에 자동 발동
- 봇이 꺼져 있던 동안 지나간 시각은 소급 적용되지 않음

### 9. 음성채널

```
--학교종 음성채널 고정
--학교종 음성채널 해제
```

| 명령 | 설명 |
|------|------|
| `음성채널 고정` | 현재 접속 중인 음성채널을 영구 지정 (봇 재시작 후에도 자동 접속) |
| `음성채널 해제` | 고정 해제 (명령 시점의 사용자 음성채널을 따름) |

- 고정하지 않은 경우, 명령을 보낸 사용자가 접속 중인 음성채널에 자동 접속

### 10. 프리셋

```
--학교종 프리셋 저장 집중모드 김동희 10분공부 5분휴식 4회반복
--학교종 프리셋 실행 집중모드
--학교종 프리셋 목록
--학교종 프리셋 삭제 집중모드
```

- 자주 쓰는 명령을 이름으로 저장
- 실행 시 저장된 명령이 그대로 실행됨
- 서버 재시작 후에도 유지

### 11. 통계

```
--학교종 통계
--학교종 통계 김동희
```

| 명령 | 설명 |
|------|------|
| `통계` | 오늘의 전체 공부/휴식 시간 요약 |
| `통계 이름` | 해당 사용자의 최근 7일간 기록 |

- 봇이 켜져 있던 동안 실제 관측 시간만 집계
- ~30초 간격으로 자동 기록

### 12. 출석

```
--학교종 출석
```

오늘 공부 60분 이상이면 ✅, 미만이면 ❌로 표시합니다.

### 13. 상태 패널 (Discord Embed)

```
--학교종 패널
--학교종 패널 해제
--학교종 패널 새로고침
```

| 명령 | 설명 |
|------|------|
| `패널` | Embed 메시지를 생성하고 10초마다 자동 갱신 |
| `패널 해제` | 자동 갱신 중지 |
| `패널 새로고침` | 즉시 패널 갱신 |

패널에는 개요, 타이머 목록, 쉬는시간 목록, 오늘 통계·출석이 포함됩니다.

### 14. 상태 / 도움말

```
--학교종 상태
--학교종 도움말
--학교종 help
```

---

## 실행 방법

### A. Windows 단일 실행파일 (설치 불필요)

1. GitHub → **Actions** 탭 → **Build Windows EXE** → **Run workflow** 클릭
2. 완료 후 Artifacts에서 `학교종-windows.zip` 다운로드 → 압축 해제
3. `학교종.exe` 실행 → 첫 실행 시 토큰 입력 → `.env` 자동 저장

```
학교종.exe      ← 실행파일 (ffmpeg 내장)
bell.mp3        ← 종소리 파일 (선택, exe 옆에 배치)
.env            ← 첫 실행 후 자동 생성
```

### B. Python으로 직접 실행

**요구사항**
- Python 3.11 이상
- ffmpeg (`brew install ffmpeg` / `sudo apt install ffmpeg`)

```bash
pip install -r requirements.txt
```

`.env` 파일 생성:
```
DISCORD_TOKEN=여기에_봇_토큰
```

실행:
```bash
python bot.py
```

---

## 음성 안내

### 동작 방식

1. 타이머 전환(공부↔휴식), 쉬는시간 시작/종료 시 자동 안내
2. 종소리(`bell.mp3` 또는 `bell.wav`) 재생 후 TTS 음성 안내
3. TTS는 edge-tts(한국어 SunHi Neural) 우선, 실패 시 gTTS로 폴백
4. 생성된 TTS 파일은 `tts_cache/`에 캐시되어 재사용

### 설정

- `bell.mp3` (또는 `bell.wav`)를 프로젝트 루트에 배치 — 없으면 TTS만 재생
- 음성채널에 접속한 상태로 타이머 명령을 입력하면 봇이 해당 채널에 자동 접속
- `--학교종 음성채널 고정`으로 채널을 영구 지정 가능

### 필요 권한

- `Connect` — 음성채널 접속
- `Speak` — 음성 재생

### 필요 패키지

- `edge-tts` (또는 `gTTS` 폴백)
- FFmpeg — `brew install ffmpeg` / `sudo apt install ffmpeg`

---

## Discord 봇 토큰 발급

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. **Bot** 탭 → Token 복사
3. **Bot** 탭 → Privileged Gateway Intents → **Message Content Intent** 활성화
4. **OAuth2** → URL Generator → `bot` 스코프 선택
5. 권한: `Connect`, `Speak`, `Send Messages`, `Read Messages`
6. 생성된 초대 링크로 서버에 봇 초대

---

## 프로젝트 구조

```
School-Bell-Discord-Bot/
├── bot.py                                 # 엔트리포인트 래퍼 (→ app.main)
├── requirements.txt                       # Python 의존성
├── .env.example                           # 환경변수 예시
├── school_bell.spec                       # PyInstaller 빌드 스펙 (Windows exe)
├── bell.mp3                               # 종소리 파일 (선택)
├── state.json                             # 길드 상태 영속 저장소 (자동 생성)
├── tts_cache/                             # TTS 오디오 캐시 디렉토리 (자동 생성)
├── .github/
│   └── workflows/
│       └── build-windows.yml              # GitHub Actions: Windows exe 빌드
└── app/
    ├── __init__.py
    ├── main.py                            # 엔트리포인트 — .env 로딩, 토큰 입력, bot.run()
    ├── config.py                          # 상수, 로깅, 경로, KST 타임존
    ├── bot/
    │   ├── __init__.py
    │   └── client.py                      # Discord 클라이언트 (이벤트 핸들러, TTS, 음성,
    │                                      #   알림, 상태 패널, 통계, 출석, 도움말)
    ├── domain/
    │   ├── __init__.py
    │   ├── models.py                      # 도메인 모델 — Timer, BreakEntry, GuildState
    │   └── commands.py                    # 커맨드 데이터클래스 (23종)
    ├── services/
    │   ├── __init__.py
    │   ├── guild_state_service.py         # 길드별 상태·락·태스크·큐 레지스트리
    │   ├── scheduler_service.py           # 스케줄러 루프 (0.5초 틱)
    │   │                                  #   — 타이머 전환, 쉬는시간 발동/해제,
    │   │                                  #     자동 종료, 통계 저장, 음성 유지
    │   ├── timer_service.py               # 타이머 상태 조작 — 시작, 종료, 일시정지,
    │   │                                  #   재개, 남은시간 수정, 통계 누적
    │   └── break_service.py               # 쉬는시간 CRUD — 일회성, 정규, 강제 종료
    ├── parsers/
    │   ├── __init__.py
    │   └── command_parser.py              # 토큰 기반 명령어 파서 — 한국어 자연어 입력을
    │                                      #   CommandClass 리스트로 변환
    ├── repositories/
    │   ├── __init__.py
    │   └── state_repository.py            # state.json 읽기/쓰기 — GuildState 직렬화
    └── utils/
        ├── __init__.py
        └── time_utils.py                  # 시간 유틸 — KST 현재 시각, 다음 발동 시각,
                                           #   MM:SS 포맷, 한국어 기간 포맷, 토큰 파싱
```

### 모듈 상세 설명

#### `app/main.py` — 엔트리포인트

- PyInstaller exe 환경과 소스 실행 환경을 자동 감지
- `.env` 파일에서 `DISCORD_TOKEN`을 로딩
- 토큰이 없으면 콘솔에서 입력받아 `.env`에 자동 저장
- `bot.run(token)`으로 Discord 클라이언트 실행

#### `app/config.py` — 설정

| 상수 | 설명 |
|------|------|
| `_BASE_DIR` | 프로젝트 루트 경로 (exe/소스 자동 감지) |
| `_FFMPEG` | ffmpeg 실행 경로 |
| `KST` | Asia/Seoul 타임존 |
| `STATE_FILE` | `state.json` 경로 |
| `PREFIX` | 명령어 접두사 (`--학교종`) |
| `TTS_CACHE` | TTS 캐시 디렉토리 |
| `log` | 로거 인스턴스 |

#### `app/domain/models.py` — 도메인 모델

**Timer** — 개인 타이머 상태

| 필드 | 타입 | 설명 |
|------|------|------|
| `study_sec` | `int` | 공부 시간 (초) |
| `rest_sec` | `int` | 휴식 시간 (초) |
| `channel_id` | `int` | 명령이 입력된 텍스트 채널 ID |
| `mode` | `str` | 현재 페이즈 (`"study"` / `"rest"`) |
| `phase_end_at` | `float` | 현재 페이즈 종료 Unix 타임스탬프 |
| `remaining_on_pause` | `float \| None` | 전체 쉬는시간에 의한 일시정지 시 남은 시간 |
| `remaining_on_personal_pause` | `float \| None` | 개인 일시정지 시 남은 시간 |
| `auto_stop_cycles` | `int \| None` | N회 반복 자동 종료 설정값 |
| `cycle_count` | `int` | 현재까지 완료한 사이클 수 |
| `auto_stop_ts` | `float \| None` | 자동 종료 시각 (Unix 타임스탬프) |
| `last_accounted_at` | `float` | 마지막 통계 누적 시각 |

**BreakEntry** — 쉬는시간 엔트리

| 필드 | 타입 | 설명 |
|------|------|------|
| `label` | `str` | 쉬는시간 이름 (예: `"점심시간"`) |
| `hhmm` | `str` | 발동 시각 (`"12:00"`) |
| `duration_sec` | `int` | 쉬는시간 길이 (초) |
| `next_ts` | `float` | 다음 발동 Unix 타임스탬프 |

**GuildState** — 서버(길드)별 전체 상태

| 필드 | 설명 |
|------|------|
| `timers` | 개인 타이머 딕셔너리 (`{이름: Timer}`) |
| `presets` | 프리셋 딕셔너리 (`{이름: 명령 문자열}`) |
| `breaks` | 일회성 쉬는시간 리스트 |
| `recurring_breaks` | 정규쉬는시간 리스트 |
| `stats` | 일별·개인별 통계 (`{날짜: {이름: {study, rest}}}`) |
| `pause_until` | 전체 일시정지 종료 시각 |
| `last_channel_id` | 마지막 명령 채널 ID |
| `last_voice_channel_id` | 마지막 음성 채널 ID |
| `pinned_voice_channel_id` | 고정된 음성 채널 ID |
| `status_panel_channel_id` | 상태 패널 채널 ID |
| `status_panel_message_id` | 상태 패널 메시지 ID |

#### `app/domain/commands.py` — 커맨드 데이터클래스

파서가 사용자 입력을 변환하는 23종의 커맨드 클래스:

| 커맨드 | 필드 | 용도 |
|--------|------|------|
| `SetTimerCommand` | name, study_sec, rest_sec, auto_stop_cycles, auto_stop_hhmm | 타이머 시작 |
| `StopTimerCommand` | name | 타이머 종료 |
| `PersonalPauseCommand` | name | 개인 일시정지 |
| `PersonalResumeCommand` | name | 개인 재개 |
| `SetRemainingCommand` | name, seconds | 남은시간 수정 |
| `ShutdownAllCommand` | — | 전체 종료 |
| `AddBreakCommand` | label, hhmm, duration_sec | 쉬는시간 등록 |
| `BreakListCommand` | — | 쉬는시간 목록 |
| `BreakDeleteCommand` | label | 쉬는시간 삭제 |
| `BreakEndCommand` | — | 쉬는시간 강제 종료 |
| `RecurringBreakAddCommand` | label, hhmm, duration_sec | 정규쉬는시간 추가 |
| `RecurringBreakListCommand` | — | 정규쉬는시간 목록 |
| `RecurringBreakDeleteCommand` | label | 정규쉬는시간 삭제 |
| `PresetSaveCommand` | name, content | 프리셋 저장 |
| `PresetRunCommand` | name | 프리셋 실행 |
| `PresetListCommand` | — | 프리셋 목록 |
| `PresetDeleteCommand` | name | 프리셋 삭제 |
| `VoicePinCommand` | — | 음성채널 고정 |
| `VoiceUnpinCommand` | — | 음성채널 해제 |
| `StatusCommand` | — | 상태 출력 |
| `StatsCommand` | name (선택) | 통계 출력 |
| `AttendanceCommand` | — | 출석 출력 |
| `HelpCommand` | — | 도움말 |
| `OpenPanelCommand` | — | 패널 생성 |
| `ClosePanelCommand` | — | 패널 해제 |
| `RefreshPanelCommand` | — | 패널 새로고침 |

#### `app/parsers/command_parser.py` — 명령어 파서

- `parse_command(raw)` — 문자열을 토큰으로 분리 후 순차적으로 커맨드 리스트 생성
- 한국어 자연어 입력을 지원하는 토큰 기반 파서
- `time_tok(s)` — `"10분공부"` → `("study", 600)` 형태로 시간+모드 파싱
- `dur_tok(s)` — `"30분"` → `1800` 형태로 순수 기간 파싱
- 하나의 명령에서 여러 타이머를 동시에 파싱 가능

#### `app/services/scheduler_service.py` — 스케줄러

- `guild_scheduler(gid)` — 길드별 0.5초 틱 비동기 루프
- 매 틱마다 수행하는 작업:
  1. **쉬는시간 발동 체크** — 일회성 + 정규쉬는시간의 `next_ts` 확인, 도달 시 모든 타이머 일시정지
  2. **일시정지 해제 체크** — `pause_until` 도달 시 모든 타이머 재개
  3. **타이머 전환** — `phase_end_at` 도달 시 공부↔휴식 전환, 알림 발송
  4. **자동 종료** — 사이클 수 / 종료 시각 도달 시 타이머 자동 삭제
  5. **통계 누적** — ~30초 간격으로 공부/휴식 시간을 `stats`에 기록
  6. **음성 유지** — 상태가 있으면 음성채널 연결 유지
- `ensure_scheduler(gid)` / `cancel_scheduler(gid)` — 스케줄러 생명주기 관리

#### `app/services/timer_service.py` — 타이머 서비스

순수 상태 조작 함수 (부수 효과 없음):

| 함수 | 설명 |
|------|------|
| `set_timer()` | 타이머 생성/재설정, GuildState에 Timer 추가 |
| `stop_timer()` | 타이머 삭제, 최종 통계 누적 |
| `shutdown_all()` | 모든 타이머·쉬는시간 삭제 |
| `do_personal_pause()` | 개인 타이머 일시정지 |
| `do_personal_resume()` | 개인 타이머 재개 |
| `do_set_remaining()` | 남은시간 수정 (phase_end_at 재계산) |
| `timer_pause()` | 전체 쉬는시간에 의한 일시정지 |
| `timer_resume()` | 전체 쉬는시간 종료 시 재개 |
| `accumulate_stats()` | 경과 시간을 일별 통계에 누적 |

#### `app/services/break_service.py` — 쉬는시간 서비스

| 함수 | 설명 |
|------|------|
| `add_break()` | 일회성 쉬는시간 등록 |
| `list_breaks()` | 일회성 쉬는시간 목록 |
| `delete_break()` | 일회성 쉬는시간 삭제 |
| `add_recurring_break()` | 정규쉬는시간 등록 |
| `list_recurring_breaks()` | 정규쉬는시간 목록 |
| `delete_recurring_break()` | 정규쉬는시간 삭제 |
| `break_end()` | 현재 진행 중인 쉬는시간 강제 종료 |

#### `app/services/guild_state_service.py` — 길드 상태 레지스트리

길드별로 분리된 런타임 객체를 관리:

| 레지스트리 | 타입 | 용도 |
|------------|------|------|
| `guild_states` | `dict[int, GuildState]` | 길드별 상태 |
| `guild_locks` | `defaultdict[int, asyncio.Lock]` | 명령 직렬화 락 |
| `guild_tasks` | `dict[int, asyncio.Task]` | 스케줄러 태스크 |
| `voice_queues` | `dict[int, asyncio.Queue]` | 음성 오디오 큐 |
| `voice_workers` | `dict[int, asyncio.Task]` | 음성 워커 태스크 |
| `panel_tasks` | `dict[int, asyncio.Task]` | 패널 갱신 태스크 |

#### `app/repositories/state_repository.py` — 영속성

- `load_state()` — `state.json` → `dict` 로딩
- `save_state(guild_states)` — `GuildState` 객체들을 JSON으로 직렬화하여 저장
- 봇 시작 시 `on_ready`에서 로딩, 상태 변경 시 즉시 저장

#### `app/utils/time_utils.py` — 시간 유틸리티

| 함수 | 설명 |
|------|------|
| `now_ts()` | KST 기준 현재 Unix 타임스탬프 |
| `next_occurrence_ts(hhmm)` | HH:MM의 다음 발동 시각 (오늘 또는 내일) |
| `fmt_mm_ss(seconds)` | `"12:34"` 형태로 포맷 |
| `fmt_dur(sec)` | `"1시간 30분 20초"` 형태로 포맷 |
| `time_tok(s)` | `"10분공부"` → `("study", 600)` 파싱 |
| `dur_tok(s)` | `"30분"` → `1800` 파싱 |

#### `app/bot/client.py` — Discord 클라이언트

프로젝트의 핵심 모듈로, 다음을 담당합니다:

**TTS 생성**
- `_make_tts()` — edge-tts(한국어 SunHi Neural) → gTTS 폴백
- `_get_tts_path_keyed()` — 캐시 키 기반 TTS 파일 생성/재사용

**음성 관리**
- `ensure_voice_connected()` — 음성채널 연결 (쿨다운, 동시 연결 방지 락, 끊어진 클라이언트 정리)
- `ensure_voice_disconnected()` — 음성채널 해제
- `_play_voice_audio()` — FFmpegOpusAudio로 음성 재생 (비동기 완료 대기)
- `_voice_worker()` — 비동기 큐 워커 (bell + TTS 순차 재생)

**알림**
- `notify_transition()` — 타이머 전환 시 텍스트 + 음성 알림
- `notify_break_event()` — 쉬는시간 시작 시 알림
- `notify_resume()` — 쉬는시간 종료 시 알림

**상태 패널**
- `build_status_embed()` — Discord Embed 생성 (개요, 타이머, 쉬는시간, 통계)
- `panel_updater_loop()` — 10초 간격 자동 갱신 루프
- `update_status_panel()` — Embed 메시지 수정

**이벤트 핸들러**
- `on_ready` — 봇 로그인, state.json에서 상태 복구, 스케줄러·패널 재시작
- `on_message` — 명령 파싱 → 23종 커맨드 핸들러 실행 → 응답 전송

---

## 아키텍처

### 처리 흐름

```
사용자 메시지 (--학교종 ...)
       ↓
   on_message()
       ↓
   parse_command() → [Command, Command, ...]
       ↓
   async with guild_lock:    ← 길드별 직렬화
       ↓
   각 Command 핸들러 실행
   ├─ GuildState 수정
   ├─ state.json 저장
   ├─ 스케줄러 시작/중지
   ├─ 패널 갱신 (비동기)
   └─ 음성 큐에 오디오 추가
       ↓
   응답 메시지 전송
```

### 스케줄러 루프 (0.5초 틱)

```
guild_scheduler(gid)
   ↓ 매 0.5초
   ├─ 쉬는시간 발동 체크 → 타이머 전체 일시정지 + 알림
   ├─ 일시정지 해제 체크 → 타이머 전체 재개 + 알림
   ├─ 타이머 전환 체크 → 공부↔휴식 전환 + 알림
   ├─ 자동 종료 체크 → 사이클/시각 도달 시 삭제
   ├─ 통계 누적 (~30초 간격)
   └─ 음성 채널 유지
```

### 음성 오디오 흐름

```
이벤트 발생 (전환/쉬는시간)
       ↓
   play_event_audio() → Queue에 추가
       ↓
   _voice_worker() (비동기 루프)
   ├─ TTS 생성/캐시 조회
   ├─ 음성채널 연결 (쿨다운, 락)
   ├─ bell.mp3 재생
   └─ TTS 재생
```

---

## 의존성

### Python 패키지 (`requirements.txt`)

| 패키지 | 용도 |
|--------|------|
| `discord.py[voice]>=2.3.0` | Discord API + 음성 지원 (PyNaCl 자동 포함) |
| `edge-tts>=6.1.9` | Microsoft Edge TTS (한국어 신경망 음성) |
| `gTTS>=2.5.0` | Google TTS (edge-tts 폴백) |
| `python-dotenv>=1.0.0` | .env 파일 로딩 |
| `tzdata>=2024.1` | Windows 타임존 데이터 |

### 시스템 의존성

| 도구 | 설치 방법 |
|------|-----------|
| FFmpeg | macOS: `brew install ffmpeg` / Ubuntu: `sudo apt install ffmpeg` |

Windows exe 빌드에는 FFmpeg가 내장됩니다.

---

## state.json 구조

```json
{
  "길드ID": {
    "last_channel_id": 123456789,
    "pinned_voice_channel_id": null,
    "presets": {
      "집중모드": "김동희 10분공부 5분휴식 4회반복"
    },
    "timers": {
      "김동희": {
        "study_sec": 600,
        "rest_sec": 300,
        "channel_id": 123456789,
        "mode": "study",
        "phase_end_at": 1709900000.0,
        "remaining_on_pause": null,
        "remaining_on_personal_pause": null,
        "auto_stop_cycles": 4,
        "cycle_count": 1,
        "auto_stop_ts": null
      }
    },
    "breaks": [
      {
        "label": "점심시간",
        "hhmm": "12:00",
        "duration_sec": 3600,
        "next_ts": 1709900000.0
      }
    ],
    "recurring_breaks": [],
    "stats": {
      "2026-03-08": {
        "김동희": {
          "study": 3600.0,
          "rest": 1200.0
        }
      }
    },
    "status_panel_channel_id": 123456789,
    "status_panel_message_id": 987654321
  }
}
```

---

## GitHub Actions (Windows exe 빌드)

`.github/workflows/build-windows.yml`

- **트리거**: 수동 실행 (workflow_dispatch) 또는 `v*` 태그 푸시
- **빌드 과정**:
  1. Python 3.12 설정
  2. 의존성 설치 (PyInstaller 포함)
  3. FFmpeg 다운로드 (Windows GPL 빌드)
  4. PyInstaller로 단일 exe 빌드 (`school_bell.spec`)
  5. Artifact 업로드: `학교종-windows.zip` (30일 보관)
