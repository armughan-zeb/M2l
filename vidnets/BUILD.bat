@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Vidnets Studio - Complete Windows Distribution Builder
cd /d "%~dp0"

echo ============================================================
echo   VIDNETS STUDIO - COMPLETE WINDOWS DISTRIBUTION BUILD
echo ============================================================
echo.
echo This build creates:
echo   1. End-user installer:
echo      dist\Vidzeo-Installer.exe
echo.
echo   2. Debug unpacked app:
echo      dist\win-unpacked\Vidnets Studio v1.exe
echo.
echo   3. Distribution logo files:
echo      dist\Vidnets-Studio-icon.ico
echo      dist\Vidnets-Studio-logo.png
echo.
echo The installer is configured to create Desktop and Start Menu shortcuts.
echo Private production API keys are blocked from the packaged app.
echo This is the only supported Windows distribution build launcher.
echo.

node scripts\build-installer.js
set "BUILD_EXIT=%ERRORLEVEL%"

if not "%VIDZEO_BUILD_NO_PAUSE%"=="1" pause
exit /b %BUILD_EXIT%
