# -*- mode: python ; coding: utf-8 -*-
import os
import sys

base_dir = os.path.abspath(SPECPATH if 'SPECPATH' in globals() else '.')
assets_dir = os.path.join(base_dir, 'app', 'assets')

if sys.platform.startswith('win'):
    icon_file = os.path.join(assets_dir, 'icon.ico')
elif sys.platform == 'darwin':
    icon_file = os.path.join(assets_dir, 'AppIcon.icns')
else:
    icon_file = os.path.join(assets_dir, 'icon.png')

datas = [
    (assets_dir, os.path.join('app', 'assets')),
]

hiddenimports = [
    'yt_dlp',
    'youtube_transcript_api',
    'psutil',
    'pydantic',
    'requests',
    'imageio_ffmpeg',
    'app.downloader.speed_optimizer',
    'app.downloader.metadata_purifier',
    'app.downloader.channel_assets_fetcher',
    'app.utils.range_parser',
    'app.utils.title_matcher',
    'app.utils.filename',
    'app.utils.logger',
    'app.ui.dialogs.download_statistics_dialog',
]

a = Analysis(
    ['main.py'],
    pathex=[base_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform.startswith('win'):
    # Standalone single-file executable for Windows
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='MultiDownloader',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_file if os.path.exists(icon_file) else None,
    )
else:
    # macOS .app bundle
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='MultiDownloader',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=[icon_file] if os.path.exists(icon_file) else None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='MultiDownloader',
    )
    app = BUNDLE(
        coll,
        name='MultiDownloader.app',
        icon=icon_file if os.path.exists(icon_file) else None,
        bundle_identifier='com.antigravity.multidownloader',
    )
