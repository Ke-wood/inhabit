@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" pythonw inhabit.py panel 8788
