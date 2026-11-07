@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Vidnets Studio - Setup

cd /d "%~dp0"

echo.
echo  Vidnets Studio - Installing Dependencies...
echo.

if not exist "tools" mkdir tools
if not exist "output" mkdir output
if not exist "temp" mkdir temp
if not exist "logs" mkdir logs
if not exist "tmp" mkdir tmp

echo [1/3] Python...

if exist "tools\python\python.exe" (
    set "PY_EXE=tools\python\python.exe"
    echo       [OK] Using bundled Python
    goto CheckFFmpeg
)

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto NeedLocalPython
python -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_EXE=python"
    echo       [OK] Using system Python
    goto CheckFFmpeg
)

:NeedLocalPython
if exist "tools\python\python.exe" (
    set "PY_EXE=tools\python\python.exe"
    echo       [OK] Using bundled Python
    goto CheckFFmpeg
)

echo       Downloading Python 3.10...
if not exist "tools\python" mkdir tools\python
powershell -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip' -OutFile 'tools\python.zip' -UseBasicParsing"
if not exist "tools\python.zip" (
    echo       [ERROR] Download failed. Install Python 3.10+ from python.org
    pause
    exit /b 1
)
powershell -Command "$ProgressPreference='SilentlyContinue'; Expand-Archive 'tools\python.zip' 'tools\python' -Force"
del "tools\python.zip" 2>nul
echo python310.zip> "tools\python\python310._pth"
echo .>> "tools\python\python310._pth"
echo Lib\site-packages>> "tools\python\python310._pth"
echo import site>> "tools\python\python310._pth"
powershell -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'tools\get-pip.py' -UseBasicParsing"
"tools\python\python.exe" "tools\get-pip.py" --no-warn-script-location >nul 2>&1
del "tools\get-pip.py" 2>nul
set "PY_EXE=tools\python\python.exe"
echo       [OK] Python installed

:CheckFFmpeg
echo [2/3] FFmpeg...

where ffmpeg >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo       [OK] Using system FFmpeg
    goto InstallPackages
)
if exist "tools\ffmpeg\ffmpeg.exe" (
    echo       [OK] Using bundled FFmpeg
    goto InstallPackages
)

echo       Downloading FFmpeg...
if not exist "tools\ffmpeg" mkdir tools\ffmpeg
powershell -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'tools\ffmpeg.zip' -UseBasicParsing } catch { Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'tools\ffmpeg.zip' -UseBasicParsing }"
if not exist "tools\ffmpeg.zip" (
    echo       [ERROR] Download failed
    pause
    exit /b 1
)
powershell -Command "$ProgressPreference='SilentlyContinue'; Expand-Archive 'tools\ffmpeg.zip' 'tools\ffmpeg-tmp' -Force"
for /r "tools\ffmpeg-tmp" %%f in (ffmpeg.exe) do copy "%%f" "tools\ffmpeg\ffmpeg.exe" >nul
for /r "tools\ffmpeg-tmp" %%f in (ffprobe.exe) do copy "%%f" "tools\ffmpeg\ffprobe.exe" >nul
rmdir /s /q "tools\ffmpeg-tmp" 2>nul
del "tools\ffmpeg.zip" 2>nul
echo       [OK] FFmpeg installed

:InstallPackages
set "PATH=%cd%\tools\ffmpeg;%cd%\tools\python;%cd%\tools\python\Scripts;%PATH%"

echo [3/3] Python packages...

"!PY_EXE!" -c "import flask, flask_cors, requests, duckduckgo_search, gallery_dl, PIL, numpy, pydub, soundfile, scipy, edge_tts" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo       [OK] All packages present
    goto Finished
)

echo       Installing packages...
"!PY_EXE!" -m pip install --upgrade pip --quiet --no-input
"!PY_EXE!" -m pip install flask flask-cors requests groq together --quiet --no-input
"!PY_EXE!" -m pip install "numpy>=1.24.0,<2.0.0" Pillow pydub soundfile scipy --quiet --no-input
"!PY_EXE!" -m pip install transformers openai-whisper faster-whisper --quiet --no-input
"!PY_EXE!" -m pip install duckduckgo-search gallery-dl --quiet --no-input 2>nul
"!PY_EXE!" -m pip install torch==2.2.0 torchaudio==2.2.0 --index-url https://download.pytorch.org/whl/cpu --quiet --no-input
echo       [OK] All packages installed

:Finished
echo.
echo  Setup complete!
echo.

if "%1"=="--auto" exit /b 0

pause
