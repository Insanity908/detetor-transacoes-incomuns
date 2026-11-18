@echo off
rem Liga o Detetor de Fraudes Financeiras (servidor waitress) e abre o navegador.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo O sistema ainda nao esta instalado. Execute primeiro o instalar.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" servidor.py --abrir
if errorlevel 1 pause
