# Cerebro grande de NOVA en Oracle Cloud

NOVA piensa en tu PC con un modelo chico y rápido. Para las preguntas difíciles puede usar un modelo más grande (Qwen3 8B) en un servidor gratis de Oracle. Es más listo, pero **lento** (el servidor gratis no tiene tarjeta gráfica): una respuesta puede tardar de 1 a 5 minutos. Por eso solo se usa cuando tú lo pides, empezando con **«piénsalo bien:»**.

## 1. Crear el servidor (en cloud.oracle.com)

1. **Crear una instancia de VM**, nombre `nova-cerebro`.
2. Imagen: **Canonical Ubuntu 24.04**. Unidad: **Ampere → VM.Standard.A1.Flex**, **2 OCPU y 12 GB** («Siempre gratis elegible»).
3. Red: nueva, con IP pública.
4. Claves SSH: **Generar par de claves → Descargar clave privada**. Guárdala en `C:\Users\<tú>\.ssh\nova-cerebro.key`.
5. Volumen de arranque: **100 GB**. Crear y esperar a «En ejecución». Anota la **IP pública**.

## 2. Instalar el cerebro (desde PowerShell en tu PC)

```powershell
cd "<carpeta de NOVA>\oracle"
scp -i $HOME\.ssh\nova-cerebro.key instalar-cerebro.sh ubuntu@IP_PUBLICA:
ssh -i $HOME\.ssh\nova-cerebro.key ubuntu@IP_PUBLICA
bash instalar-cerebro.sh
```

Si `ssh` se queja de los permisos de la clave: clic derecho en el archivo → Propiedades → Seguridad → Opciones avanzadas → deshabilitar herencia y dejar solo a tu usuario.

A la mitad aparece un enlace de **Tailscale**: ábrelo e inicia sesión (con Google, por ejemplo). Tarda unos 15 minutos (baja unos 5 GB). Al final imprime dos líneas con la dirección y la clave.

## 3. Conectar tu PC

1. Instala **Tailscale** en Windows (tailscale.com/download) e inicia sesión con **la misma cuenta**.
2. Abre `%APPDATA%\NOVA\config.json` (si no existe, créalo con `{ }`) y agrega las dos líneas que imprimió el servidor, por ejemplo:

```json
{
  "cerebro_endpoint": "http://100.x.y.z:8080",
  "cerebro_clave": "la-clave-que-salió"
}
```

3. Reinicia NOVA y prueba: `piénsalo bien: ¿cómo organizarías el proyecto NOVORA-LINK?`

El servidor solo escucha dentro de tu red privada de Tailscale y pide la clave; desde internet no se ve. Si el servidor está apagado o no responde, NOVA te avisa y contesta con su modelo de siempre.

¿Quieres un modelo más grande (14B, aún más lento)? En el servidor: `MODELO=14b bash instalar-cerebro.sh`.
