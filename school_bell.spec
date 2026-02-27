# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — school bell bot Windows single-file build

import importlib
import os
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# -- Collect PyNaCl native binaries --
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all('nacl')
nacl_binaries += collect_dynamic_libs('nacl')

# Explicitly find _sodium.pyd and co-located DLLs
try:
    _nacl_mod = importlib.import_module('nacl._sodium')
    _sodium_path = _nacl_mod.__file__
    if _sodium_path:
        nacl_binaries.append((_sodium_path, 'nacl'))
        print(f"[spec] found _sodium: {_sodium_path}")
        _nacl_dir = os.path.dirname(_sodium_path)
        for f in os.listdir(_nacl_dir):
            if f.endswith(('.dll', '.pyd')) and f != os.path.basename(_sodium_path):
                full = os.path.join(_nacl_dir, f)
                nacl_binaries.append((full, 'nacl'))
                print(f"[spec] found extra binary: {full}")
        _libs_dir = os.path.join(_nacl_dir, '.libs')
        if os.path.isdir(_libs_dir):
            for f in os.listdir(_libs_dir):
                if f.endswith('.dll'):
                    full = os.path.join(_libs_dir, f)
                    nacl_binaries.append((full, '.'))
                    print(f"[spec] found .libs dll: {full}")
except Exception as e:
    print(f"[spec] WARNING: nacl._sodium import failed: {e}")

print(f"[spec] nacl_binaries count: {len(nacl_binaries)}")
print(f"[spec] nacl_hiddenimports count: {len(nacl_hiddenimports)}")

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
        # PyNaCl
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
        # Windows timezone DB
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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
