@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" pythonw tools\panel.py 8788
