@echo off
rem Instalador de NOVA: doble clic aqui. No uses "Ejecutar como administrador".
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
echo.
pause
