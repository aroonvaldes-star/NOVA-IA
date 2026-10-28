import sys

import pytest

from nova import politica as p


@pytest.mark.parametrize("orden", [
    "diskpart", "format E:", "DiskPart.EXE /s x", "bcdedit /set", "manage-bde -unlock D:", "reg add HKCU\\x",
    "powershell -c dir", "cmd /c dir", "schtasks /create", "winget install x", "del /s C:\\x", "rd /s x",
    "robocopy a b /MIR", "icacls x /grant", "sudo ls", "net user", "wsl", "msiexec /i x.msi", "nova chat",
    "C:\\Windows\\System32\\diskpart.exe", "build.bat", "gradlew.bat assembleDebug", "script.ps1", "x.vbs",
    "python -c print(1)", "git status & del x", "echo %PATH%", "dir > salida.txt", "type ^x", "git log | more",
    "copy a.txt C:\\Windows\\a.txt", "type \\\\.\\PhysicalDrive0", "notepad CON", "type a.txt:oculto",
    "type \\\\servidor\\compartida\\a.txt", "copy x C:\\ProgramData\\x",
])
def test_prohibido(orden):
    assert p.clasificar(orden).nivel == p.PROHIBIDO


@pytest.mark.parametrize("orden", [
    "git status", "git log --oneline", "git diff", "whoami", "hostname", "tasklist", "ipconfig",
    "dotnet --info", "git branch", "git remote -v", "findstr TODO archivo.cs", "systeminfo",
])
def test_solo_lectura(orden):
    assert p.clasificar(orden).nivel == p.SOLO_LECTURA


@pytest.mark.parametrize("orden", [
    "dotnet build", "git commit -m hola", "git -c core.pager=x log", "git diff --output=x", "git branch nueva",
    "git branch -D vieja", "git remote add o url", "ipconfig /flushdns", "whoami /priv", "adb devices",
    "python script.py", "robocopy a b", "tree /f", "git push",
])
def test_confirmar(orden):
    assert p.clasificar(orden).nivel == p.CONFIRMAR


def test_git_nunca_autoejecutable():
    d = p.clasificar("git status")
    assert d.nivel == p.SOLO_LECTURA and not d.autoejecutable
    assert p.clasificar("hostname").autoejecutable


def test_dividir_comillas():
    assert p.dividir('git commit -m "hola mundo"') == ["git", "commit", "-m", "hola mundo"]
    with pytest.raises(ValueError):
        p.dividir('git commit -m "sin cerrar')


@pytest.mark.parametrize("ruta,ok", [
    ("C:\\Users\\aroon\\Documents\\a.txt", True),
    ("D:\\Proyectos\\NOVORA-LINK\\Program.cs", True),
    ("E:\\Respaldo", True),
    ("C:\\Windows\\System32\\x.dll", False),
    ("c:\\program files\\x", False),
    ("C:\\ProgramData\\x", False),
    ("C:\\Users\\aroon\\AppData\\Local\\x", False),
    ("C:\\Users\\aroon\\.ssh\\id_rsa", False),
    ("C:\\Users\\aroon\\proyecto\\.git\\config", False),
    ("C:\\Users\\otro\\Documents\\a.txt", False),
    ("C:\\Users\\Public\\Documents\\a.txt", True),
    ("\\\\servidor\\compartida\\a.txt", False),
    ("\\\\.\\PhysicalDrive0", False),
    ("\\\\?\\C:\\x", False),
    ("C:\\x\\a.txt:flujo", False),
    ("C:\\x\\CON", False),
    ("C:\\x\\nul.txt", False),
    ("C:\\x\\..\\Windows", False),
    ("relativa\\a.txt", False),
    ("C:\\$Recycle.Bin\\x", False),
    ("C:\\System Volume Information", False),
    ("C:\\Users\\aroon\\NTUSER.DAT", False),
    ("C:\\x\\archivo. ", False),
])
def test_rutas_windows(ruta, ok):
    assert p.revisar_ruta_texto(ruta, windows=True, usuario="aroon").ok is ok


def test_unidad_excluida():
    assert not p.revisar_ruta_texto("Z:\\trabajo", windows=True, excluidas=["Z:"]).ok


@pytest.mark.parametrize("ruta,ok", [
    ("/home/aroon/proyecto/a.py", True), ("/etc/passwd", False), ("/home/aroon/.ssh/id", False),
    ("relativa", False), ("/proc/1/mem", False),
])
@pytest.mark.skipif(sys.platform == "win32", reason="reglas de Linux; en Windows rigen las de Windows")
def test_rutas_posix(ruta, ok):
    assert p.revisar_ruta_texto(ruta, windows=False).ok is ok


def test_revisar_ruta_rechaza_enlaces(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "enlace").symlink_to(real)
    assert not p.revisar_ruta(str(tmp_path / "enlace" / "a.txt")).ok
    assert p.revisar_ruta(str(real / "a.txt")).ok


def test_revisar_ruta_rechaza_enlaces_duros(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    import os
    os.link(a, tmp_path / "b.txt")
    assert not p.revisar_ruta(str(a)).ok


def test_escritura_fuera_de_casa_pide_clic(tmp_path):
    c = p.revisar_ruta(str(tmp_path / "nuevo.txt"), escribir=True)
    assert c.ok and c.confirmar  # tmp no está dentro de la carpeta personal
    assert not p.revisar_ruta(str(tmp_path / "x.bat"), escribir=True).ok


def test_ejecutar_prohibido_no_corre():
    r = p.ejecutar(p.clasificar("diskpart"), None)
    assert not r["ok"] and "Prohibido" in r["salida"]


def test_ejecutar_lectura():
    r = p.ejecutar(p.clasificar("hostname"), None)
    assert r["ok"] and r["salida"].strip()


def test_no_busca_en_carpeta_actual(tmp_path, monkeypatch):
    falso = tmp_path / "hostname"
    falso.write_text("#!/bin/sh\necho FALSO\n")
    falso.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    r = p.ejecutar(p.clasificar("hostname"), None)
    assert "FALSO" not in r["salida"]
