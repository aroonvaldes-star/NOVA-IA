@echo off
rem Abre NOVA. Despues de instalar tambien puedes usar el acceso "NOVA" del menu Inicio (Ctrl+Alt+N).
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\NOVA\venv\Scripts\pythonw.exe" (
  start "" "%LOCALAPPDATA%\NOVA\venv\Scripts\pythonw.exe" -m nova servidor --abrir
) else (
  start "" pythonw -m nova servidor --abrir
)
