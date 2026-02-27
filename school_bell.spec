# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec -- school bell bot Windows single-file build

import importlib
import os
import glob
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# -- Collect PyNaCl native binaries --
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all('nacl')
nacl_binaries += collect_dynamic_libs('nacl')

# Find nacl package directory
_nacl_pkg = importlib.import_module('nacl')
_nacl_pkg_dir = os.path.dirname(_nacl_pkg.__file__)
_site_packages = os.path.dirname(_nacl_pkg_dir)

# 1) nacl.libs/ directory (delvewheel style, next to nacl/ in site-packages)
_nacl_libs = os.path.join(_site_packages, 'nacl.libs')
if os.path.isdir(_nacl_libs):
    for f in os.listdir(_nacl_libs):
        if f.endswith('.dll'):
            full = os.path.join(_nacl_libs, f)
            nacl_binaries.append((full, '.'))
            print(f"[spec] nacl.libs dll: {full}")
else:
    print(f"[spec] nacl.libs dir not found at: {_nacl_libs}")

# 2) .pyd files inside nacl/ package
for f in glob.glob(os.path.join(_nacl_pkg_dir, '**', '*.pyd'), recursive=True):
    rel = os.path.relpath(os.path.dirname(f), _site_packages)
    nacl_binaries.append((f, rel))
    print(f"[spec] nacl pyd: {f} -> {rel}")

# 3) Any DLLs directly inside nacl/ package
for f in glob.glob(os.path.join(_nacl_pkg_dir, '**', '*.dll'), recursive=True):
    nacl_binaries.append((f, '.'))
    print(f"[spec] nacl dll: {f}")

print(f"[spec] total nacl_binaries: {len(nacl_binaries)}")

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
