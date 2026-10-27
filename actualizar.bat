@echo off
rem Actualiza NOVA desde GitHub: doble clic aqui. No uses "Ejecutar como administrador".
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0actualizar.ps1" %*
echo.
pause
