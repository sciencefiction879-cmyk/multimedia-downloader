import os
import sys

icon_file = os.path.join('app', 'assets', 'icon.ico') if sys.platform.startswith('win') else os.path.join('app', 'assets', 'AppIcon.icns')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('app/assets', 'app/assets')],
    hiddenimports=['yt_dlp', 'youtube_transcript_api', 'psutil', 'pydantic', 'app.downloader.speed_optimizer', 'app.downloader.metadata_purifier', 'app.downloader.channel_assets_fetcher', 'app.utils.range_parser'],
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
    icon=icon_file if os.path.exists(icon_file) else None,
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
    bundle_identifier=None,
)
