@echo off
title ScriptIT - YouTube Script Generator
echo.
echo ============================================
echo   ScriptIT - YouTube Script Generator Pro
echo ============================================
echo.

if not exist node_modules (
    echo First run detected. Installing dependencies...
    echo.
    call npm install
    if errorlevel 1 (
        echo.
        echo ERROR: npm install failed.
        echo Make sure Node.js 18+ is installed.
        pause
        exit /b 1
    )
)

echo Starting ScriptIT...
echo.
call npm run electron

if errorlevel 1 (
    echo.
    echo Application closed with an error.
    pause
)
