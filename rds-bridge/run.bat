@echo off
REM Start RDS Bridge with config.json from this folder.
cd /d "%~dp0"
python -m rdsbridge --config config.json
pause
