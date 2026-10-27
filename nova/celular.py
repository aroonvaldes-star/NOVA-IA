"""Conexión directa celular ↔ PC por la red Wi-Fi de tu casa.

Sin nube ni servidores de terceros: el celular abre la página de NOVA que sirve
tu PC en la red local. Para que nadie más en la red pueda usarla:

  * Emparejamiento: en la PC dices «conecta mi celular» y NOVA muestra un
    código de 6 cifras (vale 5 minutos, 5 intentos). El celular lo escribe y
    recibe una llave propia que guarda; en la PC solo se guarda su huella (SHA-256).
  * Cada petición del celular lleva su llave. «Desconecta mis celulares» las
    revoca todas.
  * Desde el celular se confirman acciones normales, pero las que exigen clic
    en la PC (escribir fuera de tu carpeta o en otra unidad) no.
  * Si está instalada la biblioteca `cryptography`, la conexión va cifrada
    (HTTPS con un certificado propio de NOVA). El micrófono del celular solo
    funciona con HTTPS; sin él, el celular sirve para escribir y leer.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path

VIGENCIA_S = 300
INTENTOS = 5


def huella(llave: str) -> str:
    return hashlib.sha256(llave.encode("utf-8")).hexdigest()


def ip_local() -> str:
    """IP de la PC en la red local (no envía paquetes: solo pregunta la ruta)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def es_red_local(ip: str) -> bool:
    try:
        d = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(d, ipaddress.IPv6Address) and d.ipv4_mapped:
        d = d.ipv4_mapped
    return d.is_private or d.is_loopback or d.is_link_local


class Emparejador:
    def __init__(self, memoria):
        self.memoria = memoria
        self._codigo: str | None = None
        self._expira = 0.0
        self._intentos = 0
        self._cerrojo = threading.Lock()

    def nuevo_codigo(self) -> str:
        with self._cerrojo:
            self._codigo = f"{secrets.randbelow(10**6):06d}"
            self._expira = time.monotonic() + VIGENCIA_S
            self._intentos = 0
            return self._codigo

    def emparejar(self, codigo: str, nombre: str) -> str | None:
        """Devuelve la llave del dispositivo si el código es correcto."""
        with self._cerrojo:
            if not self._codigo or time.monotonic() > self._expira or self._intentos >= INTENTOS:
                self._codigo = None
                return None
            self._intentos += 1
            if not secrets.compare_digest(codigo.strip(), self._codigo):
                return None
            self._codigo = None
        llave = secrets.token_urlsafe(32)
        self.memoria.agregar_dispositivo(nombre or "celular", huella(llave))
        return llave

    def valido(self, llave: str | None) -> bool:
        return bool(llave) and self.memoria.dispositivo_por_huella(huella(llave)) is not None


def contexto_tls(carpeta: Path) -> ssl.SSLContext | None:
    """Certificado propio de NOVA (autofirmado, 5 años). None si falta `cryptography`."""
    cert, llave = carpeta / "celular-cert.pem", carpeta / "celular-llave.pem"
    if not (cert.is_file() and llave.is_file()):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
        except ImportError:
            return None
        clave = ec.generate_private_key(ec.SECP256R1())
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NOVA")])
        ip = ipaddress.ip_address(ip_local())
        ahora = dt.datetime.now(dt.timezone.utc)
        certificado = (
            x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(clave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - dt.timedelta(days=1))
            .not_valid_after(ahora + dt.timedelta(days=5 * 365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("nova.local"), x509.IPAddress(ip)]), critical=False)
            .sign(clave, hashes.SHA256())
        )
        carpeta.mkdir(parents=True, exist_ok=True)
        llave.write_bytes(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
        from .rutas import proteger
        proteger(llave)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, llave)
    return ctx
