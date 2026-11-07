@echo off
chcp 65001 >nul 2>&1
title Vidnets Studio
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "VIDZEO_NODE_PATH=%cd%\tools\node\node.exe"
set "PATH=%cd%\tools\ffmpeg;%cd%\tools\python;%cd%\tools\python\Scripts;%cd%\tools\node;%cd%\node_modules\.bin;%PATH%"
set "ELECTRON_EXE=%cd%\node_modules\electron\dist\electron.exe"

set TF_CPP_MIN_LOG_LEVEL=3
set TF_ENABLE_ONEDNN_OPTS=0
set CUDA_VISIBLE_DEVICES=-1
set TRANSFORMERS_VERBOSITY=error
set PYTHONWARNINGS=ignore
set PYTHONIOENCODING=utf-8
set VIDZEO_YANDEX_CURL_IMPERSONATE=1

if not exist "%ELECTRON_EXE%" (
    echo.
    echo   [ERROR] Vidnets Studio's bundled runtime is missing.
    echo   Re-copy the complete Vidnets-Clean-Working-App folder.
    echo.
    pause
    exit /b 1
)

if not exist "node_modules\electron" (
    echo.
    echo   [ERROR] Vidnets Studio's bundled Electron runtime is missing.
    echo   Re-copy the complete Vidnets-Clean-Working-App folder.
    echo.
    pause
    exit /b 1
)

if not exist "tools\node\node.exe" (
    echo.
    echo   [ERROR] Vidnets Studio's bundled Remotion runtime is missing.
    echo   Re-copy the complete Vidnets Clean Working Folder.
    echo.
    pause
    exit /b 1
)

if exist "tools\python\python.exe" (
    set "PY_EXE=tools\python\python.exe"
    goto CheckFlask
)
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_EXE=python"
    goto CheckFlask
)
goto NeedInstall

:CheckFlask
"!PY_EXE!" -c "import flask" >nul 2>&1
if %ERRORLEVEL% EQU 0 goto Launch
goto NeedInstall

:NeedInstall
echo.
echo   First-time setup - installing dependencies...
echo.
call INSTALL.bat --auto
echo.

:Launch
rem Do not kill arbitrary processes listening on port 5000. Electron now
rem probes the preferred port and selects a free localhost fallback when
rem another application owns it.
echo.
echo   Vidnets Studio - Starting...
echo.

"%ELECTRON_EXE%" .

echo.
echo   App closed.
