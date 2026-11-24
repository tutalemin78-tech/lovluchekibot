@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Установка зависимостей...
py -m pip install -r requirements.txt
if errorlevel 1 python -m pip install -r requirements.txt
echo Запуск...
py main.py
if errorlevel 1 python main.py
pause
