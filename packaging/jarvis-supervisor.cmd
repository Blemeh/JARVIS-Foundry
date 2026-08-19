@echo off
setlocal EnableExtensions
rem Stable launcher. Identity does not depend on cwd, PYTHONPATH, or a checkout.
set "PYTHONPATH="
set "PYTHONHOME="
if not defined JARVIS_INSTALL_ROOT set "JARVIS_INSTALL_ROOT=%LOCALAPPDATA%\JARVIS"
set "CURRENT_FILE=%JARVIS_INSTALL_ROOT%\CURRENT"
if not exist "%CURRENT_FILE%" (
  echo FAIL_CLOSED: CURRENT pointer missing>&2
  exit /b 2
)
set /p SHA=<"%CURRENT_FILE%"
for /f "tokens=* delims= " %%A in ("%SHA%") do set "SHA=%%A"
set "RELEASE=%JARVIS_INSTALL_ROOT%\releases\%SHA%"
set "PY=%RELEASE%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo FAIL_CLOSED: release python missing>&2
  exit /b 2
)
"%PY%" -m jarvis_foundry.deployment.launcher %*
exit /b %ERRORLEVEL%
