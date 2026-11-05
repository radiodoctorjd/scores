@echo off
rem Stops RDS Bridge from starting with Windows (asks for administrator rights).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0tools\remove-autostart.ps1\"'"
