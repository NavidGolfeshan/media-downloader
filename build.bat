@echo off
setlocal
cd /d "%~dp0"

echo === Checking bin\ (ffmpeg, ffprobe, node) ===
set MISSING=0
for %%F in (ffmpeg.exe ffprobe.exe node.exe) do (
    if not exist "bin\%%F" (
        echo   MISSING: bin\%%F
        set MISSING=1
    )
)
if "%MISSING%"=="1" (
    echo.
    echo Copy the real .exe files into the bin folder first. See bin\README.txt
    exit /b 1
)

echo === Creating build environment ===
set PY=py -3
where py >nul 2>nul || set PY=python
if not exist .build-venv ( %PY% -m venv .build-venv || exit /b 1 )
call .build-venv\Scripts\activate.bat
python -m pip install --upgrade pip || exit /b 1
pip install -r requirements.txt pyinstaller || exit /b 1

echo === Building ===
pyinstaller --noconfirm --clean MediaDownloader.spec || exit /b 1

echo === Adding bin\ next to the exe ===
xcopy bin dist\MediaDownloader\bin /E /I /Y >nul

echo.
echo Done. Copy the whole folder  dist\MediaDownloader  to any Windows PC
echo and run MediaDownloader.exe
