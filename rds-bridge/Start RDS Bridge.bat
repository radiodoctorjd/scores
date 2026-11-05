@echo off
setlocal
cd /d "%~dp0"
title RDS Bridge

if exist "%~dp0RDSBridge.exe" (
    "%~dp0RDSBridge.exe" --config "%~dp0config.json" --open %*
    goto done
)

rem No RDSBridge.exe here, so run from the Python source instead.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython
%PY% -m rdsbridge --config "%~dp0config.json" --open %*
goto done

:nopython
echo.
echo  Python 3 was not found on this PC.
echo.
echo  Either download the ready-made Windows version (RDSBridge.exe, no Python needed),
echo  or install Python 3 from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:done
if errorlevel 1 pause
