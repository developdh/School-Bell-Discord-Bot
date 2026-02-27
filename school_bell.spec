# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — 학교종 봇 Windows 단일 실행파일 빌드
# 빌드 전에 같은 폴더에 ffmpeg.exe 가 있어야 합니다.

from PyInstaller.utils.hooks import collect_all

# PyNaCl 네이티브 바이너리(_sodium.pyd + libsodium.dll) 자동 수집
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all('nacl')

a = Analysis(
    ["bot.py"],
    pathex=[],
    binaries=[("ffmpeg.exe", ".")] + nacl_binaries,
    datas=[] + nacl_datas,
    hiddenimports=[
        # discord.py voice
        "discord.gateway",
        "discord.opus",
        "discord.voice_client",
        "discord.player",
        "discord.backoff",
    ] + nacl_hiddenimports + [
        # TTS
        "edge_tts",
        "edge_tts.communicate",
        "edge_tts.submaker",
        "gtts",
        "gtts.tts",
        # dotenv
        "dotenv",
        # Windows 타임존 DB
        "tzdata",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="학교종",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,         # 콘솔 창 표시 (로그 확인용)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
