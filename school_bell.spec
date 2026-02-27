# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — 학교종 봇 Windows 단일 실행파일 빌드
# 빌드 전에 같은 폴더에 ffmpeg.exe 가 있어야 합니다.

import importlib
import os
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

# ── PyNaCl 네이티브 바이너리 강제 수집 ────────────────────────────────────────
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all('nacl')
nacl_binaries += collect_dynamic_libs('nacl')

# _sodium.pyd 를 명시적으로 찾아서 추가 (collect_all 이 놓칠 경우 대비)
try:
    _nacl_mod = importlib.import_module('nacl._sodium')
    _sodium_path = _nacl_mod.__file__
    if _sodium_path:
        nacl_binaries.append((_sodium_path, 'nacl'))
        print(f"[spec] _sodium.pyd 발견: {_sodium_path}")
        # 같은 디렉토리의 .dll 파일도 포함
        _nacl_dir = os.path.dirname(_sodium_path)
        for f in os.listdir(_nacl_dir):
            if f.endswith(('.dll', '.pyd')) and f != os.path.basename(_sodium_path):
                full = os.path.join(_nacl_dir, f)
                nacl_binaries.append((full, 'nacl'))
                print(f"[spec] 추가 DLL 발견: {full}")
        # .libs 디렉토리 체크 (일부 PyNaCl 버전에서 libsodium.dll 위치)
        _libs_dir = os.path.join(_nacl_dir, '.libs')
        if os.path.isdir(_libs_dir):
            for f in os.listdir(_libs_dir):
                if f.endswith('.dll'):
                    full = os.path.join(_libs_dir, f)
                    nacl_binaries.append((full, '.'))
                    print(f"[spec] .libs DLL 발견: {full}")
except Exception as e:
    print(f"[spec] WARNING: nacl._sodium import 실패: {e}")

print(f"[spec] nacl_binaries 수: {len(nacl_binaries)}")
print(f"[spec] nacl_hiddenimports 수: {len(nacl_hiddenimports)}")

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
        # PyNaCl — collect_all 이 놓칠 경우를 대비한 명시적 목록
        "nacl",
        "nacl._sodium",
        "nacl.bindings",
        "nacl.bindings._sodium",
        "nacl.bindings.crypto_aead",
        "nacl.bindings.crypto_box",
        "nacl.bindings.crypto_generichash",
        "nacl.bindings.crypto_hash",
        "nacl.bindings.crypto_pwhash",
        "nacl.bindings.crypto_scalarmult",
        "nacl.bindings.crypto_secretbox",
        "nacl.bindings.crypto_secretstream",
        "nacl.bindings.crypto_shorthash",
        "nacl.bindings.crypto_sign",
        "nacl.bindings.randombytes",
        "nacl.bindings.utils",
        "nacl.public",
        "nacl.secret",
        "nacl.signing",
        "nacl.encoding",
        "nacl.hash",
        "nacl.utils",
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
