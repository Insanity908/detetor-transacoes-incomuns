@echo off
rem Instala o Detetor de Fraudes Financeiras: ambiente Python, dependencias, base de dados e modelos.
setlocal
cd /d "%~dp0"
echo.
echo === Detetor de Fraudes Financeiras - instalacao ===
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo [ERRO] Python nao encontrado. Instale o Python 3.11 ou mais recente em https://www.python.org
  echo        e marque a opcao "Add python.exe to PATH" durante a instalacao.
  goto :falhou
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo [ERRO] E preciso o Python 3.11 ou mais recente.
  %PY% --version
  goto :falhou
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] A criar o ambiente virtual .venv ...
  %PY% -m venv .venv || goto :falhou
) else (
  echo [1/3] Ambiente virtual .venv ja existe.
)

echo [2/3] A instalar as dependencias ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :falhou

echo [3/3] A preparar a configuracao, a base de dados, e os modelos ...
".venv\Scripts\python.exe" gerir.py preparar || goto :falhou

echo.
echo Instalacao concluida. Para ligar o sistema, execute o iniciar.bat
echo.
pause
exit /b 0

:falhou
echo.
echo A instalacao nao terminou. Veja a mensagem de erro acima.
pause
exit /b 1
