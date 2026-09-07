#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=========================================="
echo " Building MultiDownloader for macOS"
echo "=========================================="

if [ -f "$DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$DIR/.venv/bin/python"
elif command -v python3 &> /dev/null; then
    VENV_PYTHON="$(command -v python3)"
elif command -v python &> /dev/null; then
    VENV_PYTHON="$(command -v python)"
else
    echo "Python not found. Please ensure dependencies are installed."
    exit 1
fi

# 1. Create macOS .icns from PNG
echo "-> Generating macOS .icns application icon..."
ICON_PNG="$DIR/app/assets/icon.png"
ICONSET_DIR="$DIR/app/assets/AppIcon.iconset"
ICNS_FILE="$DIR/app/assets/AppIcon.icns"

mkdir -p "$ICONSET_DIR"
sips -z 16 16     "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16.png" > /dev/null 2>&1 || true
sips -z 32 32     "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" > /dev/null 2>&1 || true
sips -z 32 32     "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32.png" > /dev/null 2>&1 || true
sips -z 64 64     "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" > /dev/null 2>&1 || true
sips -z 128 128   "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128.png" > /dev/null 2>&1 || true
sips -z 256 256   "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" > /dev/null 2>&1 || true
sips -z 256 256   "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256.png" > /dev/null 2>&1 || true
sips -z 512 512   "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" > /dev/null 2>&1 || true
sips -z 512 512   "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512.png" > /dev/null 2>&1 || true
sips -z 1024 1024 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" > /dev/null 2>&1 || true

iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE" > /dev/null 2>&1 || true
rm -rf "$ICONSET_DIR"

# 2. Build .app Bundle with PyInstaller
echo "-> Compiling macOS standalone .app bundle..."
rm -rf "$DIR/build" "$DIR/dist"

"$VENV_PYTHON" -m PyInstaller \
    --name="MultiDownloader" \
    --windowed \
    --onedir \
    --icon="$ICNS_FILE" \
    --add-data="app/assets:app/assets" \
    --hidden-import="yt_dlp" \
    --hidden-import="youtube_transcript_api" \
    --hidden-import="psutil" \
    --hidden-import="pydantic" \
    --hidden-import="app.downloader.speed_optimizer" \
    --hidden-import="app.downloader.metadata_purifier" \
    --hidden-import="app.downloader.channel_assets_fetcher" \
    --noconfirm \
    --clean \
    main.py

echo "-> Application bundle built at: dist/MultiDownloader.app"

# Sync to local workspace folder
echo "-> Updating local workspace MultiDownloader.app..."
rm -rf "$DIR/MultiDownloader.app"
cp -R "$DIR/dist/MultiDownloader.app" "$DIR/MultiDownloader.app"

# Sync to /Applications if installed there and not in CI
if [ "$CI" != "true" ] && ([ -d "/Applications/MultiDownloader.app" ] || [ -w "/Applications" ]); then
    echo "-> Installing updated build directly to /Applications/MultiDownloader.app..."
    rm -rf "/Applications/MultiDownloader.app"
    cp -R "$DIR/dist/MultiDownloader.app" "/Applications/MultiDownloader.app"
    echo "-> /Applications/MultiDownloader.app updated successfully!"
fi

# 3. Create DMG Installer
echo "-> Creating macOS DMG Installer..."
DMG_PATH="$DIR/dist/MultiDownloader.dmg"
rm -f "$DMG_PATH"

if command -v create-dmg &> /dev/null; then
    create-dmg \
        --volname "MultiDownloader Installer" \
        --volicon "$ICNS_FILE" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "MultiDownloader.app" 175 190 \
        --hide-extension "MultiDownloader.app" \
        --app-drop-link 425 190 \
        "$DMG_PATH" \
        "$DIR/dist/MultiDownloader.app" > /dev/null 2>&1 || true
fi

# If create-dmg didn't produce DMG (or fallback), use native hdiutil
if [ ! -f "$DMG_PATH" ]; then
    echo "-> Using hdiutil fallback to build DMG..."
    hdiutil create -volname "MultiDownloader" -srcfolder "$DIR/dist/MultiDownloader.app" -ov -format UDZO "$DMG_PATH"
fi

cp -f "$DMG_PATH" "$DIR/dist/MultiDownloader-v2.8.0.dmg"

echo "=========================================="
echo " Build Success!"
echo " App: dist/MultiDownloader.app"
echo " DMG: dist/MultiDownloader.dmg"
echo " DMG: dist/MultiDownloader-v2.8.0.dmg"
echo "=========================================="
