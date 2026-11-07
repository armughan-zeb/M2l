@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0CREATE_PACKAGE.bat"
exit /b %ERRORLEVEL%
