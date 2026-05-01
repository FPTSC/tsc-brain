@echo off
cd /d "%~dp0"

:: Crea cartella logs se non esiste
if not exist "logs" mkdir logs

:: Timestamp per il log
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "D=%%c-%%b-%%a"
for /f "tokens=1-2 delims=:." %%a in ("%time: =0%") do set "T=%%a%%b"
set "LOGFILE=logs\pipeline_%D%_%T%.log"

:: Carica variabili da .env (salta commenti e righe vuote)
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /v /r "^#" .env ^| findstr /v /r "^$"`) do set "%%a=%%b"

echo [%date% %time%] Avvio pipeline TSC-Brain >> "%LOGFILE%"

python -c "import sys; sys.path.insert(0, '.'); import logging; logging.basicConfig(level=logging.INFO, format='[%%(asctime)s] %%(levelname)s %%(message)s'); from src.pipeline import run; n=run(); print(f'Pipeline completata: {n} recording elaborati')" >> "%LOGFILE%" 2>&1

echo [%date% %time%] Fine pipeline >> "%LOGFILE%"
