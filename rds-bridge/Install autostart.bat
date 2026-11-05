@echo off
rem Makes RDS Bridge start automatically when Windows starts (asks for administrator rights).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0tools\install-autostart.ps1\"'"
