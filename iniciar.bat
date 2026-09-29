@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title RINO

rem --- 1. Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [ERRO] Python 3.10 ou superior nao encontrado. Instale em https://www.python.org/downloads/ marcando "Add Python to PATH".
  pause
  exit /b 1
)

rem --- 2. Ambiente virtual
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERRO] Nao foi possivel criar o ambiente virtual.
    pause
    exit /b 1
  )
)

rem --- 3. Ativar
call ".venv\Scripts\activate.bat"

rem --- 4. Dependencias (so instala se faltar algo)
python -c "import flask, requests, bs4, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Instalando dependencias ...
  python -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias. Verifique a conexao com a internet.
    pause
    exit /b 1
  )
)
rem yt-dlp e opcional: falha aqui nao impede o uso
python -c "import yt_dlp" >nul 2>nul
if errorlevel 1 (
  echo Instalando yt-dlp opcional ...
  python -m pip install --disable-pip-version-check -r requirements-opcional.txt >nul 2>nul
)

rem --- 5. .env opcional
if not exist ".env" if exist ".env.example" copy /y ".env.example" ".env" >nul

rem --- 6. Iniciar
echo.
echo Iniciando... o navegador abrira em http://127.0.0.1:5000  (feche esta janela para encerrar)
python run.py
if errorlevel 1 pause
endlocal
