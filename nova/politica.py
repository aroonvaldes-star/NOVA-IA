"""Política de órdenes y de rutas de NOVA (falla cerrada).

Toda orden que el chat, el agente o la voz quieran ejecutar pasa por aquí y
recibe uno de tres niveles:

    SOLO_LECTURA  se puede ejecutar sin preguntar (salvo git en el agente)
    CONFIRMAR     se ejecuta solo con tu confirmación (código de 4 cifras)
    PROHIBIDO     nunca se ejecuta, se pida como se pida

Reglas que no cambian:
  * Una sola orden, sin shell: nunca cmd.exe ni PowerShell.
  * Solo ejecutables .exe resueltos a ruta absoluta en carpetas fijas,
    nunca en la carpeta actual.
  * Nada de discos, particiones, arranque, BitLocker, registro, servicios,
    usuarios ni borrado masivo.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

SOLO_LECTURA = "SOLO_LECTURA"
CONFIRMAR = "CONFIRMAR"
PROHIBIDO = "PROHIBIDO"

PROHIBIDOS: dict[str, str] = {}


def _grupo(motivo: str, nombres: str) -> None:
    for nombre in nombres.split():
        PROHIBIDOS[nombre] = motivo


_grupo("discos, particiones y volúmenes",
       "diskpart format chkdsk chkntfs fsutil mountvol defrag convert label subst vssadmin "
       "wbadmin cipher sdelete diskshadow recover mkfs fdisk parted dd wipefs mount umount")
_grupo("arranque y firmware",
       "bcdedit bcdboot bootsect reagentc bootrec shutdown mdsched reboot poweroff halt efibootmgr grub-install")
_grupo("cifrado (BitLocker)", "manage-bde repair-bde cryptsetup")
_grupo("privilegios, usuarios y sistema",
       "runas sudo su doas net net1 netsh sc reg regedit regini takeown icacls cacls secedit auditpol "
       "gpupdate wmic schtasks at dism sfc pnputil devcon wevtutil setx chown chmod systemctl crontab")
_grupo("instaladores y paquetes", "msiexec winget choco scoop npx apt apt-get dpkg snap flatpak")
_grupo("intérpretes y lanzadores que esquivarían la política",
       "cmd powershell pwsh wscript cscript mshta rundll32 regsvr32 installutil certutil bitsadmin "
       "forfiles explorer start wsl bash sh zsh conhost cmstp odbcconf hh env xargs nohup")
_grupo("borrado y permisos masivos", "del erase rd rmdir attrib rm shred")
_grupo("herramientas internas de NOVA", "nova llama-server")

EXT_NO_EJECUTABLES = {".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".lnk", ".msi", ".com", ".scr", ".hta"}
# Además de lo anterior, NOVA nunca escribe archivos que Windows ejecuta o interpreta al abrirlos.
EXT_NO_ESCRIBIBLES = EXT_NO_EJECUTABLES | {
    ".exe", ".dll", ".sys", ".drv", ".cpl", ".ocx", ".msc", ".msp", ".pif", ".reg", ".inf", ".url", ".scf",
    ".psm1", ".psd1", ".ps1xml", ".jar", ".gadget", ".application", ".appref-ms", ".settingcontent-ms",
    ".library-ms", ".search-ms", ".xll", ".wsh", ".chm",
}

# ; | & > < $ ` * ? ~ ( ) { } [ ] ! y los metacaracteres de cmd/PowerShell ^ % @
CARACTERES_SHELL = set(";|&><$`*?~(){}[]!^%@\n\r")

RUTAS_PROTEGIDAS = [
    "c:\\windows", "%systemroot%", "c:\\program files", "c:\\programdata", "c:\\recovery",
    "$recycle.bin", "system volume information", "\\\\.\\", "\\\\?\\", "physicaldrive",
    "harddiskvolume", "globalroot", "hklm", "hkey_local_machine", "appdata", ".ssh", ".gnupg",
    "/etc", "/boot", "/dev/", "/proc", "/sys/",
]

NOMBRES_DISPOSITIVO = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

GIT_LECTURA = {"status", "log", "diff", "show", "branch", "tag", "remote", "rev-parse", "ls-files", "blame", "shortlog", "describe"}
GIT_OPCIONES_PELIGROSAS = ("--output", "--ext-diff", "--textconv", "-o", "--exec", "--upload-pack", "--config", "-c")


@dataclass
class Decision:
    nivel: str
    motivo: str
    argv: list[str] = field(default_factory=list)
    autoejecutable: bool = False  # el agente puede ejecutarla sin preguntar

    @property
    def permitido(self) -> bool:
        return self.nivel != PROHIBIDO


def dividir(orden: str) -> list[str]:
    """Divide una orden en argumentos respetando comillas dobles. No interpreta nada más."""
    partes, actual, en_comillas, hay = [], [], False, False
    for c in orden.strip():
        if c == '"':
            en_comillas = not en_comillas
            hay = True
        elif c.isspace() and not en_comillas:
            if hay:
                partes.append("".join(actual))
                actual, hay = [], False
        else:
            actual.append(c)
            hay = True
    if en_comillas:
        raise ValueError("comillas sin cerrar")
    if hay:
        partes.append("".join(actual))
    return partes


def nombre_programa(arg0: str) -> tuple[str, str]:
    base = PureWindowsPath(arg0).name if "\\" in arg0 else Path(arg0).name
    base = base.lower()
    raiz, ext = os.path.splitext(base)
    if ext == ".exe":
        return raiz, ""
    return (raiz, ext) if ext in EXT_NO_EJECUTABLES else (base, "")


def _arg_protegido(arg: str) -> str | None:
    bajo = arg.lower().replace("/", "\\") if re.match(r"^[a-z]:", arg, re.I) else arg.lower()
    for protegida in RUTAS_PROTEGIDAS:
        if protegida in bajo:
            return protegida
    if bajo.startswith("\\\\"):
        return "ruta de red (UNC)"
    if ":" in bajo and "://" not in bajo:
        resto = re.sub(r"^[a-z]:", "", bajo)
        if ":" in resto and ("\\" in resto or re.search(r"\.\w+:", resto)):
            return "flujo alternativo de datos"
    for comp in re.split(r"[\\/]", bajo):
        if comp.split(".")[0] in NOMBRES_DISPOSITIVO and comp:
            return f"nombre de dispositivo ({comp})"
    return None


def clasificar(orden: str | list[str]) -> Decision:
    if isinstance(orden, str):
        if any(c in CARACTERES_SHELL for c in orden):
            return Decision(PROHIBIDO, "contiene caracteres de shell; NOVA ejecuta una sola orden, sin shell")
        try:
            argv = dividir(orden)
        except ValueError as exc:
            return Decision(PROHIBIDO, str(exc))
    else:
        argv = list(orden)
        if any(c in CARACTERES_SHELL for a in argv for c in a):
            return Decision(PROHIBIDO, "contiene caracteres de shell", argv)
    if not argv:
        return Decision(PROHIBIDO, "orden vacía")

    prog, ext = nombre_programa(argv[0])
    if ext:
        return Decision(PROHIBIDO, f"los archivos {ext} no se ejecutan directamente; NOVA te muestra la orden para que la ejecutes tú", argv)
    if prog in PROHIBIDOS:
        return Decision(PROHIBIDO, f"{prog}: {PROHIBIDOS[prog]}", argv)
    if prog in {"python", "python3", "py", "node"} and len(argv) > 1 and argv[1] in {"-c", "-e", "--eval"}:
        return Decision(PROHIBIDO, "código en línea sin archivo", argv)
    for arg in argv[1:]:
        protegida = _arg_protegido(arg)
        if protegida:
            return Decision(PROHIBIDO, f"toca una ruta protegida: {protegida}", argv)
    args = [a.lower() for a in argv[1:]]

    if prog == "robocopy" and any(a in {"/mir", "/purge", "/move", "/mov"} for a in args):
        return Decision(PROHIBIDO, "robocopy con borrado o movimiento masivo", argv)

    lectura = _es_lectura(prog, args)
    if lectura:
        return Decision(SOLO_LECTURA, "solo lectura", argv, autoejecutable=prog != "git")
    return Decision(CONFIRMAR, "puede cambiar cosas; necesita tu confirmación", argv)


def _es_lectura(prog: str, args: list[str]) -> bool:
    simples = {"hostname", "where", "tasklist", "systeminfo", "findstr", "fc", "whoami", "ver", "pwd", "uname", "ls", "cat", "head", "tail", "wc", "grep"}
    if prog in simples:
        if prog == "whoami" and any("/priv" in a for a in args):
            return False
        return True
    if prog == "ipconfig":
        return not any(a in {"/release", "/renew", "/flushdns", "/registerdns", "/setclassid"} for a in args)
    if prog == "sort":
        return not any(a.startswith("/o") for a in args)
    if prog == "tree":
        return "/f" not in args
    if prog == "dotnet":
        return args in (["--info"], ["--list-sdks"], ["--list-runtimes"], ["--version"])
    if prog == "git":
        if not args or args[0].startswith("-"):
            return False  # opciones globales antes del subcomando
        sub, resto = args[0], args[1:]
        if sub not in GIT_LECTURA:
            return False
        if any(a.startswith(GIT_OPCIONES_PELIGROSAS) for a in resto):
            return False
        if sub in {"branch", "tag"} and any(not a.startswith("-") or a in {"-d", "-D", "-m", "-M", "--delete", "--move", "-f", "--force"} for a in resto):
            return False
        if sub == "remote" and resto not in ([], ["-v"], ["--verbose"]):
            return False
        return True
    return False


# --------------------------------------------------------------------- rutas

PROTEGIDAS_WINDOWS = [
    PureWindowsPath("C:/Windows"), PureWindowsPath("C:/Program Files"), PureWindowsPath("C:/Program Files (x86)"),
    PureWindowsPath("C:/ProgramData"), PureWindowsPath("C:/Recovery"),
]
COMPONENTES_PROHIBIDOS = {
    "appdata", "$recycle.bin", "system volume information", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
}
PROTEGIDAS_POSIX = ["/etc", "/boot", "/dev", "/proc", "/sys", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var", "/root/.ssh"]

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000


@dataclass
class ChequeoRuta:
    ok: bool
    motivo: str = ""
    confirmar: bool = False  # escribir aquí necesita confirmación por clic o teclado
    ruta: Path | None = None


def revisar_ruta_texto(texto: str, windows: bool | None = None, usuario: str | None = None,
                       excluidas: list[str] | None = None) -> ChequeoRuta:
    """Revisión solo por texto (sin tocar el disco). Sirve también para probar reglas de Windows en Linux."""
    windows = sys.platform == "win32" if windows is None else windows
    if not texto or "\x00" in texto:
        return ChequeoRuta(False, "ruta vacía o inválida")
    if windows:
        if texto.startswith(("\\\\", "//")):
            return ChequeoRuta(False, "rutas de red y de dispositivo no permitidas")
        bajo = texto.lower()
        for marca in ("globalroot", "physicaldrive", "harddiskvolume"):
            if marca in bajo:
                return ChequeoRuta(False, "acceso a dispositivo en bruto")
        p = PureWindowsPath(texto)
        if not p.drive or not re.match(r"^[a-zA-Z]:$", p.drive):
            return ChequeoRuta(False, "usa una ruta absoluta con letra de unidad")
        if ":" in texto[2:]:
            return ChequeoRuta(False, "flujos alternativos de datos no permitidos")
        if excluidas and p.drive[0].upper() in {e.strip(":").upper() for e in excluidas}:
            return ChequeoRuta(False, f"la unidad {p.drive} está excluida en tu configuración")
        for protegida in PROTEGIDAS_WINDOWS:
            if _dentro(p, protegida):
                return ChequeoRuta(False, f"carpeta protegida de Windows ({protegida})")
        partes = p.parts[1:]
        for comp in partes:
            c = comp.lower()
            if c == "..":
                return ChequeoRuta(False, "no se permiten '..'")
            if re.search(r"~\d", c):
                return ChequeoRuta(False, f"nombre corto 8.3 no permitido ({comp}); usa el nombre completo")
            if c.startswith("."):
                return ChequeoRuta(False, f"carpeta o archivo oculto ({comp})")
            if c in COMPONENTES_PROHIBIDOS:
                return ChequeoRuta(False, f"ubicación protegida ({comp})")
            if c.startswith("ntuser.dat"):
                return ChequeoRuta(False, "registro del usuario")
            if c.split(".")[0] in NOMBRES_DISPOSITIVO or c.rstrip(" .") != c:
                return ChequeoRuta(False, f"nombre reservado de Windows ({comp})")
        if len(partes) >= 2 and partes[0].lower() == "users" and usuario and partes[1].lower() not in {usuario.lower(), "public"}:
            return ChequeoRuta(False, "perfil de otro usuario")
        return ChequeoRuta(True, ruta=None)
    p = Path(texto)
    if not p.is_absolute():
        return ChequeoRuta(False, "usa una ruta absoluta")
    if ".." in p.parts:
        return ChequeoRuta(False, "no se permiten '..'")
    for protegida in PROTEGIDAS_POSIX:
        if str(p) == protegida or str(p).startswith(protegida + "/"):
            return ChequeoRuta(False, f"carpeta del sistema ({protegida})")
    for comp in p.parts[1:]:
        if comp.startswith("."):
            return ChequeoRuta(False, f"carpeta o archivo oculto ({comp})")
    return ChequeoRuta(True)


def _dentro(p: PureWindowsPath, base: PureWindowsPath) -> bool:
    a = [x.lower() for x in p.parts]
    b = [x.lower() for x in base.parts]
    return a[: len(b)] == b


def revisar_ruta(texto: str, escribir: bool = False, excluidas: list[str] | None = None) -> ChequeoRuta:
    """Revisión completa: texto + disco (enlaces, junctions, ocultos, enlaces duros, OneDrive en la nube)."""
    texto = os.path.expandvars(os.path.expanduser(texto.strip().strip('"')))
    chequeo = revisar_ruta_texto(texto, usuario=os.environ.get("USERNAME") or os.environ.get("USER"), excluidas=excluidas)
    if not chequeo.ok:
        return chequeo
    ruta = Path(texto)
    # Ningún componente existente puede ser enlace simbólico, junction o punto de montaje.
    actual = Path(ruta.anchor)
    for comp in ruta.parts[1:]:
        actual = actual / comp
        if not os.path.lexists(actual):
            break
        try:
            st = os.lstat(actual)
        except OSError:
            return ChequeoRuta(False, "no se pudo revisar la ruta")
        atributos = getattr(st, "st_file_attributes", 0)
        if stat.S_ISLNK(st.st_mode) or atributos & FILE_ATTRIBUTE_REPARSE_POINT:
            return ChequeoRuta(False, f"enlace o junction en la ruta ({actual})")
        if atributos & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
            return ChequeoRuta(False, f"elemento oculto o de sistema ({actual.name})")
    if ruta.exists() and ruta.is_file():
        st = os.lstat(ruta)
        if st.st_nlink > 1:
            return ChequeoRuta(False, "archivo con varios enlaces duros")
        if getattr(st, "st_file_attributes", 0) & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS):
            return ChequeoRuta(False, "archivo de OneDrive solo en la nube (leerlo lo descargaría)")
    confirmar = False
    if escribir:
        casa = Path.home().resolve()
        try:
            ruta.resolve().relative_to(casa)
        except ValueError:
            confirmar = True
        if ruta.suffix.lower() in EXT_NO_ESCRIBIBLES or ruta.name.lower() == "desktop.ini":
            return ChequeoRuta(False, "NOVA no escribe ejecutables, scripts ni accesos directos")
        if "startup" in str(ruta).lower() or "inicio" in [c.lower() for c in ruta.parts]:
            return ChequeoRuta(False, "NOVA no escribe en la carpeta de Inicio")
    return ChequeoRuta(True, confirmar=confirmar, ruta=ruta)


# --------------------------------------------------------------- ejecución

def _carpetas_ejecutables(solo_lectura: bool) -> list[str]:
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        local = os.environ.get("LOCALAPPDATA", "")
        carpetas = [
            os.path.join(pf, "Git", "cmd"), os.path.join(pf, "dotnet"),
            os.path.join(local, "Android", "Sdk", "platform-tools"),
            os.path.join(local, "Programs", "Python", "Python312"),
        ]
        if solo_lectura:
            carpetas.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
        return [c for c in carpetas if c]
    return ["/usr/local/bin", "/usr/bin", "/bin"]


def resolver_ejecutable(prog: str, solo_lectura: bool) -> str | None:
    """Busca el programa solo en carpetas fijas, nunca en la carpeta actual."""
    ruta = os.pathsep.join(_carpetas_ejecutables(solo_lectura))
    encontrado = shutil.which(prog + (".exe" if sys.platform == "win32" else ""), path=ruta)
    return os.path.abspath(encontrado) if encontrado else None


def ejecutar(decision: Decision, cwd: str | None, timeout: int = 60) -> dict:
    """Ejecuta una orden ya clasificada y (si hacía falta) confirmada."""
    if decision.nivel == PROHIBIDO:
        return {"ok": False, "salida": f"Prohibido: {decision.motivo}"}
    prog, _ = nombre_programa(decision.argv[0])
    exe = resolver_ejecutable(prog, decision.nivel == SOLO_LECTURA)
    if not exe:
        return {"ok": False, "salida": f"No encontré {prog} en las carpetas permitidas."}
    if cwd:
        chequeo = revisar_ruta(cwd)
        if not chequeo.ok:
            return {"ok": False, "salida": f"Carpeta de trabajo no permitida: {chequeo.motivo}"}
    entorno = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOME", "LANG", "USERNAME", "LOCALAPPDATA", "APPDATA"}}
    try:
        r = subprocess.run(
            [exe, *decision.argv[1:]], cwd=cwd or None, capture_output=True, text=True, timeout=timeout,
            shell=False, env=entorno, stdin=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "salida": f"Se pasó del límite de {timeout} s."}
    except OSError as exc:
        return {"ok": False, "salida": f"No se pudo ejecutar: {exc}"}
    salida = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    return {"ok": r.returncode == 0, "codigo": r.returncode, "salida": salida[-20000:]}
