@echo off
echo ==========================================
echo  Building MultiDownloader for Windows (x64)
echo ==========================================

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m PyInstaller --clean MultiDownloader.spec

echo.
if exist "dist\MultiDownloader.exe" (
    echo ==========================================
    echo  Build Successful!
    echo  Binary: dist\MultiDownloader.exe
    echo ==========================================
) else (
    echo [ERROR] Build failed! Check the log output above.
)
pause
