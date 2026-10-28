@echo off
rem Instala OmniRoute para el cerebro grande de NOVA: doble clic aqui. No uses "Ejecutar como administrador".
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0omniroute.ps1" %*
echo.
pause
