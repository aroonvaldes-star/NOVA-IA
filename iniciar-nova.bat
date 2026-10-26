@echo off
rem Abre NOVA en el navegador mientras no tengas el envoltorio de escritorio compilado.
cd /d "%~dp0"
start "" pythonw -m nova servidor --abrir
