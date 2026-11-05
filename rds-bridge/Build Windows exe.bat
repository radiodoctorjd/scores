@echo off
rem Builds dist\RDSBridge\RDSBridge.exe on this PC (needs Python 3.8+ and internet access for pip).
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
)
%PY% -m pip install --upgrade pyinstaller pyserial || goto failed
%PY% packaging\build_windows.py || goto failed
echo.
echo Done. The program is in the dist\RDSBridge folder (and dist\RDSBridge-windows.zip).
pause
exit /b 0

:failed
echo.
echo Build failed, see the messages above.
pause
exit /b 1
