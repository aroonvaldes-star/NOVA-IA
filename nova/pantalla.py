"""Lo que NOVA ve de tu pantalla: la ventana en la que trabajas y, si se lo pides, una captura.

* `ventana_activa()` dice qué programa y qué título tiene la ventana que usas
  (sin contar la de NOVA). Sirve para que NOVA sepa en qué andas.
* `Observador` anota en segundo plano las últimas ventanas que usaste.
* `capturar()` toma la pantalla principal (escondiendo un momento la ventana de
  NOVA) y la devuelve como PNG reducido, listo para un modelo con visión.
* `leer_texto()` saca el texto de una captura con el OCR que trae Windows,
  para cuando el modelo no tiene visión.

Solo funciona en Windows; en otros sistemas todo devuelve vacío o un error claro.
La captura nunca se guarda: el PNG temporal del OCR se borra al terminar.
"""

from __future__ import annotations

import base64
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field

TITULO_NOVA = "NOVA"
ES_WINDOWS = sys.platform == "win32"


@dataclass
class Ventana:
    titulo: str
    programa: str

    def __str__(self) -> str:
        return f"{self.titulo} ({self.programa})" if self.programa else self.titulo


@dataclass
class Captura:
    png: bytes
    ancho: int
    alto: int
    ventana: Ventana | None = None

    def data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png).decode()


# --------------------------------------------------------------- Win32

if ES_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    HWND, HDC, HBITMAP, HGDIOBJ = wintypes.HWND, wintypes.HDC, wintypes.HANDLE, wintypes.HGDIOBJ
    _u32.GetForegroundWindow.restype = HWND
    _u32.GetWindow.argtypes, _u32.GetWindow.restype = [HWND, wintypes.UINT], HWND
    _u32.GetTopWindow.argtypes, _u32.GetTopWindow.restype = [HWND], HWND
    _u32.GetWindowTextLengthW.argtypes = [HWND]
    _u32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.IsWindowVisible.argtypes = [HWND]
    _u32.IsIconic.argtypes = [HWND]
    _u32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetDC.argtypes, _u32.GetDC.restype = [HWND], HDC
    _u32.ReleaseDC.argtypes = [HWND, HDC]
    _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _g32.CreateCompatibleDC.argtypes, _g32.CreateCompatibleDC.restype = [HDC], HDC
    _g32.CreateCompatibleBitmap.argtypes, _g32.CreateCompatibleBitmap.restype = [HDC, ctypes.c_int, ctypes.c_int], HBITMAP
    _g32.SelectObject.argtypes, _g32.SelectObject.restype = [HDC, HGDIOBJ], HGDIOBJ
    _g32.SetStretchBltMode.argtypes = [HDC, ctypes.c_int]
    _g32.SetBrushOrgEx.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _g32.StretchBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    _g32.GetDIBits.argtypes = [HDC, HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    _g32.DeleteObject.argtypes = [HGDIOBJ]
    _g32.DeleteDC.argtypes = [HDC]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    def _dpi_real() -> None:
        """Sin esto, Windows con escala 125 % o 150 % entrega una pantalla más chica y borrosa."""
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # por monitor v2
            return
        except (AttributeError, OSError):
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass

    _dpi_real()


def _titulo(hwnd) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _programa(hwnd) -> str:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = _k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _k32.CloseHandle(h)


def _es_nova(titulo: str) -> bool:
    return titulo == TITULO_NOVA or titulo.startswith(TITULO_NOVA + " -") or titulo.startswith(TITULO_NOVA + " —")


_IGNORAR = {"Program Manager", "Configuración", "Settings", "Centro de notificaciones", "Inicio", "Start", "Buscar", "Search"}


def ventana_activa() -> Ventana | None:
    """La ventana en la que trabajas: la de enfrente o, si es NOVA, la que está detrás."""
    if not ES_WINDOWS:
        return None
    try:
        hwnd = _u32.GetForegroundWindow()
        vistos = 0
        while hwnd and vistos < 60:
            vistos += 1
            titulo = _titulo(hwnd)
            if (titulo and not _es_nova(titulo) and titulo not in _IGNORAR
                    and _u32.IsWindowVisible(hwnd) and not _u32.IsIconic(hwnd)):
                return Ventana(titulo[:200], _programa(hwnd))
            hwnd = _u32.GetWindow(hwnd, 2)  # GW_HWNDNEXT: la siguiente en el orden de apilado
    except OSError:
        return None
    return None


def _ventanas_nova() -> list:
    """Ventanas visibles de NOVA (para esconderlas mientras se toma la captura)."""
    salida = []
    hwnd = _u32.GetTopWindow(None)
    vistos = 0
    while hwnd and vistos < 500:
        vistos += 1
        if _u32.IsWindowVisible(hwnd) and not _u32.IsIconic(hwnd) and _es_nova(_titulo(hwnd)):
            salida.append(hwnd)
        hwnd = _u32.GetWindow(hwnd, 2)
    return salida


@dataclass
class Observador:
    """Anota en segundo plano las últimas ventanas distintas que usaste."""

    maximo: int = 8
    recientes: list[tuple[float, Ventana]] = field(default_factory=list)
    _detener: threading.Event = field(default_factory=threading.Event)

    def anotar(self, v: Ventana | None) -> None:
        if not v:
            return
        if self.recientes and self.recientes[-1][1].titulo == v.titulo:
            return
        self.recientes = [r for r in self.recientes if r[1].titulo != v.titulo][-(self.maximo - 1):] + [(time.time(), v)]

    def arrancar(self, cada: float = 3.0) -> None:
        if not ES_WINDOWS:
            return

        def ciclo():
            while not self._detener.wait(cada):
                try:
                    self.anotar(ventana_activa())
                except Exception:  # noqa: BLE001 - nunca debe tumbar a NOVA
                    pass
        threading.Thread(target=ciclo, daemon=True, name="nova-ventanas").start()

    def detener(self) -> None:
        self._detener.set()

    def resumen(self) -> str:
        if not self.recientes:
            return ""
        ahora = time.time()
        actual = self.recientes[-1][1]
        antes = [f"{v} (hace {max(1, int((ahora - t) / 60))} min)" for t, v in reversed(self.recientes[:-1])][:4]
        return f"Ventana en uso: {actual}." + (" Antes: " + "; ".join(antes) + "." if antes else "")


# -------------------------------------------------------------- captura

def capturar(max_ancho: int = 1280, esconder_nova: bool = True) -> Captura:
    """Captura la pantalla principal, reducida a `max_ancho` píxeles de ancho."""
    if not ES_WINDOWS:
        raise RuntimeError("Ver la pantalla solo funciona en Windows.")
    escondidas = _ventanas_nova() if esconder_nova else []
    for h in escondidas:
        _u32.ShowWindow(h, 6)  # SW_MINIMIZE
    if escondidas:
        time.sleep(0.45)  # que termine la animación de minimizar
    try:
        ventana = ventana_activa()
        ancho, alto = _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)
        escala = min(1.0, max_ancho / max(ancho, 1))
        an, al = max(1, int(ancho * escala)), max(1, int(alto * escala))
        pantalla = _u32.GetDC(None)
        memoria = _g32.CreateCompatibleDC(pantalla)
        mapa = _g32.CreateCompatibleBitmap(pantalla, an, al)
        anterior = _g32.SelectObject(memoria, mapa)
        try:
            _g32.SetStretchBltMode(memoria, 4)  # HALFTONE: reduce sin dientes
            _g32.SetBrushOrgEx(memoria, 0, 0, None)
            if not _g32.StretchBlt(memoria, 0, 0, an, al, pantalla, 0, 0, ancho, alto, 0x00CC0020 | 0x40000000):
                raise OSError(ctypes.get_last_error(), "StretchBlt falló")
            info = _BITMAPINFOHEADER()
            info.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.biWidth, info.biHeight, info.biPlanes, info.biBitCount = an, -al, 1, 32  # alto negativo: de arriba abajo
            buf = ctypes.create_string_buffer(an * al * 4)
            if not _g32.GetDIBits(memoria, mapa, 0, al, buf, ctypes.byref(info), 0):
                raise OSError(ctypes.get_last_error(), "GetDIBits falló")
        finally:
            _g32.SelectObject(memoria, anterior)
            _g32.DeleteObject(mapa)
            _g32.DeleteDC(memoria)
            _u32.ReleaseDC(None, pantalla)
    finally:
        for h in escondidas:
            _u32.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE: vuelve sin robarte el foco
    return Captura(png_desde_bgra(buf.raw, an, al), an, al, ventana)


def png_desde_bgra(bgra: bytes, ancho: int, alto: int) -> bytes:
    """PNG RGB a partir de píxeles BGRA de arriba abajo (sin bibliotecas externas)."""
    rgb = bytearray(ancho * alto * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    fila = ancho * 3
    crudo = b"".join(b"\x00" + bytes(rgb[i * fila:(i + 1) * fila]) for i in range(alto))

    def bloque(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(crudo, 6)) + bloque(b"IEND", b""))


# ------------------------------------------------------------------ OCR

_OCR_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Esperar($op, $tipo) { $t = $asTask.MakeGenericMethod($tipo).Invoke($null, @($op)); $t.Wait(); $t.Result }
$archivo = Esperar ([Windows.Storage.StorageFile]::GetFileFromPathAsync('__RUTA__')) ([Windows.Storage.StorageFile])
$flujo = Esperar ($archivo.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decodificador = Esperar ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($flujo)) ([Windows.Graphics.Imaging.BitmapDecoder])
$mapa = Esperar ($decodificador.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$motor = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $motor) { Write-Output 'SIN_OCR'; exit 0 }
$resultado = Esperar ($motor.RecognizeAsync($mapa)) ([Windows.Media.Ocr.OcrResult])
$resultado.Lines | ForEach-Object { $_.Text }
$flujo.Dispose()
"""


def leer_texto(captura: Captura, timeout: int = 40) -> str:
    """Texto de la captura con el OCR de Windows (Windows.Media.Ocr)."""
    if not ES_WINDOWS:
        return ""
    fd, ruta = tempfile.mkstemp(prefix="nova-pantalla-", suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(captura.png)
        script = _OCR_PS.replace("__RUTA__", ruta.replace("'", "''"))
        # -EncodedCommand evita los problemas de comillas de Windows PowerShell 5.1.
        codificado = base64.b64encode(script.encode("utf-16-le")).decode()
        sistema = os.environ.get("SystemRoot", r"C:\Windows")
        ps = os.path.join(sistema, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        p = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", codificado],
                           capture_output=True, timeout=timeout, creationflags=0x08000000)
        texto = p.stdout.decode("utf-8", "replace").strip()
        if texto == "SIN_OCR":
            return ""
        return texto
    except (OSError, subprocess.TimeoutExpired):
        return ""
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass
