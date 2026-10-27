@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python nao encontrado. Instale em https://python.org/downloads
  echo        e marque a opcao "Add Python to PATH" durante a instalacao.
  pause
  exit /b 1
)

python install.py
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
