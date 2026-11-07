@echo off
setlocal
set "VIDNETS_AGENT_ROOT=%~dp0"
if exist "%VIDNETS_AGENT_ROOT%resources\server\headless_agent.py" set "VIDNETS_AGENT_ROOT=%VIDNETS_AGENT_ROOT%resources\"
if not exist "%VIDNETS_AGENT_ROOT%tools\python\python.exe" (
  echo Bundled Python is missing. Restore the complete Vidnets installation.
  exit /b 1
)
"%VIDNETS_AGENT_ROOT%tools\python\python.exe" "%VIDNETS_AGENT_ROOT%server\headless_agent.py" %*
exit /b %errorlevel%
