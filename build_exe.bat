@echo off
echo === MicBoost Build Script ===

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installing requirements...
pip install --upgrade pip
pip install -r requirements.txt

echo Building EXE with PyInstaller...
pyinstaller build.spec --noconfirm

echo.
echo Done! Your MicBoost.exe is in the "dist" folder.
pause
