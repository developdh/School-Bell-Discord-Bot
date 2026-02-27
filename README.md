# 학교종 Discord 봇

공부/휴식 타이머와 쉬는시간 알림을 음성채널 종소리 + TTS로 알려주는 Discord 봇입니다.

## 기능

- **개인 타이머** — 이름별로 공부·휴식 사이클을 반복하며, 전환 시 음성으로 안내
- **쉬는시간 알림** — 지정한 시각에 모든 타이머를 일시정지하고 종료 시 자동 재개
- **음성 안내** — 음성채널 자동 접속 후 종소리(bell.mp3) + TTS 재생
- **시간 단위** — 초 / 분 / 시간 모두 입력 가능

## 명령어

prefix: `--학교종`

| 명령어 | 설명 |
|--------|------|
| `--학교종 이름 10분공부 5분휴식` | 개인 타이머 시작 (순서 무관, 복수 등록 가능) |
| `--학교종 이름 종료` | 개인 타이머 종료 |
| `--학교종 종료` | 모든 타이머·쉬는시간 삭제 및 봇 대기 상태로 전환 |
| `--학교종 쉬는시간 점심시간 12:00 1시간` | 매일 12:00에 1시간 쉬는시간 등록 |
| `--학교종 쉬는시간 끝` | 현재 쉬는시간 즉시 종료 (스케줄은 유지) |
| `--학교종 상태` | 현재 타이머·쉬는시간 목록 출력 |
| `--학교종 도움말` | 도움말 출력 |

**시간 입력 예시:** `30초공부`, `10분휴식`, `1시간공부`, `1시간 30분` (쉬는시간 duration)

## 실행 방법

### A. Windows 단일 실행파일 (설치 불필요)

1. 이 레포를 GitHub에 올린다
2. GitHub → **Actions** 탭 → **Build Windows EXE** → **Run workflow** 클릭
3. 완료 후 Artifacts에서 `학교종-windows.zip` 다운로드 → 압축 해제
4. `학교종.exe` 실행 → 첫 실행 시 토큰 입력 → `.env` 자동 저장

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

## 음성 안내 설정

1. `bell.mp3` (종소리 파일)을 `bot.py` 옆에 배치 — 없으면 TTS만 재생
2. 음성채널에 접속한 상태로 타이머 명령을 입력하면 봇이 해당 채널에 자동 접속

봇에 필요한 Discord 권한: `Connect`, `Speak`, `Read Messages`, `Send Messages`

## Discord 봇 토큰 발급

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. Bot 탭 → Token 복사
3. Bot 탭 → Privileged Gateway Intents → **Message Content Intent** 활성화
4. OAuth2 → URL Generator → `bot` 스코프, `Connect` / `Speak` / `Send Messages` 권한 → 초대 링크로 서버 초대
