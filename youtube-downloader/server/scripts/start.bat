@echo off
REM Modo nativo (sem Docker). Deixe esta janela aberta enquanto for baixar.
cd /d "%~dp0.."
python server.py
pause
