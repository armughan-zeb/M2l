@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Vidnets Studio v1 - Create Package
cd /d "%~dp0"

echo ============================================================
echo   VIDNETS STUDIO V1 - CREATE WORKING APP PACKAGE
echo ============================================================

echo.
echo This creates a fresh runnable folder from THIS repo.
echo.
echo Output:
echo   dist\Vidnets-Clean-Working-App
echo.
echo Start packaged app with:
echo   dist\Vidnets-Clean-Working-App\START_VIDNETS_STUDIO.bat
echo.
echo Optional:
echo   set VIDZEO_PACKAGE_CREATE_ZIP=1 before running to also create a zip.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create-clean-working-folder.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo.
    echo ============================================================
    echo   CLEAN WORKING FOLDER READY
    echo ============================================================
    echo.
    echo Folder:
    echo   %~dp0dist\Vidnets-Clean-Working-App
    echo.
    echo Start:
    echo   %~dp0dist\Vidnets-Clean-Working-App\START_VIDNETS_STUDIO.bat
    echo.
) else (
    echo.
    echo ============================================================
    echo   PACKAGE FAILED
    echo ============================================================
    echo.
    echo The clean folder was not created. Fix the error above and run CREATE_PACKAGE.bat again.
    echo.
)

if not "%VIDZEO_BUILD_NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
