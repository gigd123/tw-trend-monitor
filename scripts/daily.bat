@echo off
REM Daily pipeline (futures, prices, signals, forward log) for Windows Task Scheduler.
REM Assumes the virtualenv is .venv in the project root; database is data\twtm.db.
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
call .venv\Scripts\activate.bat
if not exist data mkdir data
echo ===== %date% %time% ===== >> data\daily.log
twtm daily >> data\daily.log 2>&1
