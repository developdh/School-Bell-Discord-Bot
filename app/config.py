"""
학교종 Discord 봇 — 설정 및 상수
"""
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Frozen(PyInstaller) 실행 여부 감지 ────────────────────────────────────────
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).parent
    _FFMPEG   = str(Path(sys._MEIPASS) / "ffmpeg.exe")
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent  # project root
    _FFMPEG   = "ffmpeg"

# ── Constants ─────────────────────────────────────────────────────────────────
KST        = ZoneInfo("Asia/Seoul")
STATE_FILE = _BASE_DIR / "state.json"
PREFIX     = "--학교종"
TTS_CACHE  = _BASE_DIR / "tts_cache"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("school_bell")
