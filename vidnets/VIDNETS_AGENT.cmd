@echo off
setlocal
set "VIDZEO_AUTH_MODE=local"
set "VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH=1"
set "VIDNETS_AGENT_ROOT=%~dp0"
if exist "%VIDNETS_AGENT_ROOT%resources\server\headless_agent.py" set "VIDNETS_AGENT_ROOT=%VIDNETS_AGENT_ROOT%resources\"

set "PYTHON_EXE="
if exist "%VIDNETS_AGENT_ROOT%tools\python\python.exe" (
  set "PYTHON_EXE=%VIDNETS_AGENT_ROOT%tools\python\python.exe"
) else if defined VIDNETS_PYTHON (
  set "PYTHON_EXE=%VIDNETS_PYTHON%"
) else (
  where python >nul 2>nul
  if %errorlevel% equ 0 (
    set "PYTHON_EXE=python"
  )
)

if "%PYTHON_EXE%"=="" (
  echo Python was not found. Please install Python or set VIDNETS_PYTHON.
  exit /b 1
)

"%PYTHON_EXE%" "%VIDNETS_AGENT_ROOT%server\headless_agent.py" %*
exit /b %errorlevel%
