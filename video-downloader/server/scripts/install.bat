@echo off
REM Modo nativo (sem Docker). Requer Python, ffmpeg e Node.js no PATH.
cd /d "%~dp0.."
echo Instalando/atualizando yt-dlp e yt-dlp-ejs...
python -m pip install --upgrade -r requirements.txt
echo.
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo AVISO: ffmpeg nao encontrado no PATH.
  echo Sem ele o video e o audio nao sao juntados num MP4 so.
  echo Instale em https://ffmpeg.org/download.html
) else (
  echo ffmpeg encontrado.
)
where node >nul 2>nul
if errorlevel 1 (
  echo AVISO: Node.js nao encontrado no PATH.
  echo O YouTube exige um runtime JavaScript para liberar os formatos.
  echo Sem ele o download falha com 'Requested format is not available'.
  echo Instale em https://nodejs.org e rode este script de novo.
) else (
  echo Runtime JavaScript encontrado:
  node --version
)
echo.
echo Pronto. Agora rode start.bat
pause
