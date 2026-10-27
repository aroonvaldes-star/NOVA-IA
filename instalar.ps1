# Instalador de NOVA para Windows 10/11 (x64), sin permisos de administrador.
# Archivo solo en ASCII a proposito: asi Windows PowerShell lo lee bien sin importar la codificacion.
#
# Que hace:
#   1. Busca Python 3.11+ (si falta, lo instala para tu usuario con winget).
#   2. Crea un entorno propio en %LOCALAPPDATA%\NOVA\venv e instala NOVA.
#   3. Descarga llama.cpp (CUDA 12 si tienes NVIDIA, si no Vulkan) a %LOCALAPPDATA%\NOVA\llama.
#   4. Descarga el modelo (Qwen3 4B Instruct, Q4_K_M, unos 2.5 GB) a %LOCALAPPDATA%\NOVA\models,
#      y el modelo con vista (Qwen3-VL 4B Instruct + su proyector, unos 3.3 GB) para ver tu pantalla.
#   5. Descarga el oido (whisper.cpp + modelo small) y la voz (Piper + voz es_MX).
#   6. Crea los accesos directos "NOVA" en el menu Inicio y el escritorio (Ctrl+Alt+N).
#   7. Corre `nova diagnostico` y abre NOVA.
#
# Puedes volver a ejecutarlo: lo que ya esta descargado y verificado no se baja otra vez.
#
# Opciones:
#   -Motor cuda|vulkan|cpu   fuerza una variante de llama.cpp (por defecto la detecta)
#   -SinModelo               no descarga el modelo
#   -SinVista                no descarga el modelo con vista (NOVA leera tu pantalla solo con OCR)
#   -SinVoz                  no descarga whisper.cpp ni Piper
#   -SinAbrir                no abre NOVA al terminar

param(
    [ValidateSet('auto', 'cuda', 'vulkan', 'cpu')][string]$Motor = 'auto',
    [switch]$SinModelo,
    [switch]$SinVista,
    [switch]$SinVoz,
    [switch]$SinAbrir
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Raiz = $PSScriptRoot
$Datos = Join-Path $env:LOCALAPPDATA 'NOVA'
$Venv = Join-Path $Datos 'venv'
$Descargas = Join-Path $Datos 'descargas'
$Agente = @{ 'User-Agent' = 'NOVA-instalador' }

function Paso($texto) { Write-Host "`n== $texto" -ForegroundColor Cyan }
function Bien($texto) { Write-Host "   OK  $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "   !   $texto" -ForegroundColor Yellow }

# --------------------------------------------------------------- comprobaciones

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'NOVA se instala y se usa con tu usuario normal, no como administrador.' -ForegroundColor Red
    Write-Host 'Cierra esta ventana y vuelve a abrir instalar.bat con doble clic (sin "Ejecutar como administrador").'
    exit 1
}
if (-not [Environment]::Is64BitOperatingSystem) { throw 'NOVA necesita Windows de 64 bits.' }
New-Item -ItemType Directory -Force -Path $Datos, $Descargas | Out-Null

# ---------------------------------------------------------------- utilidades

function Huella-HuggingFace($Url) {
    # Hugging Face publica el SHA256 de los archivos grandes en la cabecera X-Linked-Etag.
    try {
        $r = Invoke-WebRequest -Uri $Url -Method Head -Headers $Agente -UseBasicParsing -MaximumRedirection 0 -ErrorAction SilentlyContinue
    } catch { $r = $_.Exception.Response }
    if ($null -eq $r) { return $null }
    $clave = $r.Headers.Keys | Where-Object { $_ -ieq 'X-Linked-Etag' } | Select-Object -First 1
    if (-not $clave) { return $null }
    $e = $r.Headers[$clave]
    $e = ($e -join '').Trim('"', ' ')
    if ($e -match '^[0-9a-fA-F]{64}$') { return $e.ToLower() }
    return $null
}

function Existe-Url($Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -Method Head -Headers $Agente -UseBasicParsing
        return $r.StatusCode -lt 400
    } catch { return $false }
}

function Descargar($Url, $Destino, $Sha256 = $null) {
    if ((Test-Path $Destino) -and $Sha256) {
        if ((Get-FileHash $Destino -Algorithm SHA256).Hash.ToLower() -eq $Sha256.ToLower()) { Bien "ya estaba: $(Split-Path $Destino -Leaf)"; return }
    } elseif ((Test-Path $Destino) -and -not $Sha256) { Bien "ya estaba: $(Split-Path $Destino -Leaf)"; return }
    $parcial = "$Destino.parcial"
    Write-Host "   ... descargando $(Split-Path $Destino -Leaf)"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    for ($i = 1; $i -le 3; $i++) {
        try {
            if ($curl) {
                & $curl.Source -L --fail --retry 3 --retry-delay 3 -C - -A 'NOVA-instalador' -o $parcial $Url
                if ($LASTEXITCODE -ne 0) { throw "curl salio con $LASTEXITCODE" }
            } else {
                Invoke-WebRequest -Uri $Url -OutFile $parcial -Headers $Agente -UseBasicParsing
            }
            break
        } catch {
            if ($i -eq 3) { throw "No pude descargar $Url : $_" }
            Start-Sleep -Seconds (3 * $i)
        }
    }
    if ($Sha256) {
        $h = (Get-FileHash $parcial -Algorithm SHA256).Hash.ToLower()
        if ($h -ne $Sha256.ToLower()) {
            Remove-Item $parcial -Force
            throw "La huella SHA256 de $(Split-Path $Destino -Leaf) no coincide; lo borre por seguridad. Vuelve a intentarlo."
        }
        Bien "verificado (SHA256): $(Split-Path $Destino -Leaf)"
    }
    Move-Item $parcial $Destino -Force
}

function Ultima-Version($Repo, $Patron) {
    # llama.cpp publica sus compilaciones (b1234) como "prerelease" y marca como "latest"
    # versiones sin binarios, asi que busco la version mas reciente que traiga el archivo.
    # Pocas versiones por pedido: la respuesta completa pesa varios MB y a veces se corta.
    $url = "https://api.github.com/repos/$Repo/releases?per_page=8"
    $versiones = $null
    for ($i = 1; $i -le 3 -and -not $versiones; $i++) {
        try { $versiones = Invoke-RestMethod -Uri $url -Headers $Agente -TimeoutSec 60 }
        catch {
            Aviso "GitHub no respondio bien (intento $i de 3): $($_.Exception.Message)"
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if ($curl) {
                $texto = & $curl.Source -sSL --retry 3 -A 'NOVA-instalador' $url
                if ($LASTEXITCODE -eq 0 -and $texto) { $versiones = ($texto -join "`n") | ConvertFrom-Json }
            }
            if (-not $versiones) { Start-Sleep -Seconds (3 * $i) }
        }
    }
    if (-not $versiones) { throw "No pude consultar las versiones de $Repo en GitHub. Revisa tu conexion y vuelve a ejecutar instalar.bat." }
    foreach ($v in $versiones) {
        if (-not $v.draft -and ($v.assets | Where-Object { $_.name -match $Patron })) { return $v }
    }
    throw "No encontre en $Repo ninguna version reciente con un archivo como $Patron"
}

function Archivo-De($Version, $Patron) {
    $a = $Version.assets | Where-Object { $_.name -match $Patron } | Select-Object -First 1
    if (-not $a) { return $null }
    $sha = $null
    if ($a.PSObject.Properties.Name -contains 'digest' -and $a.digest -match '^sha256:([0-9a-f]{64})$') { $sha = $Matches[1] }
    return @{ Nombre = $a.name; Url = $a.browser_download_url; Sha = $sha }
}

function Extraer-Limpio($Zip, $Carpeta, $Exe) {
    # Extrae en una carpeta nueva y deja el .exe (y sus DLL) en la raiz de $Carpeta.
    $tmp = "$Carpeta.nuevo"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $Zip -DestinationPath $tmp -Force
    $exe = Get-ChildItem $tmp -Recurse -Filter $Exe | Select-Object -First 1
    if (-not $exe) { throw "No encontre $Exe dentro de $(Split-Path $Zip -Leaf)" }
    if (Test-Path $Carpeta) {
        # Conserva modelos y voces que ya esten ahi
        Get-ChildItem $Carpeta -File | Where-Object { $_.Extension -in '.bin', '.onnx', '.json', '.gguf' } | Move-Item -Destination $exe.DirectoryName -Force
        Remove-Item $Carpeta -Recurse -Force
    }
    Move-Item $exe.DirectoryName $Carpeta
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
}

# ---------------------------------------------------------------- 1. Python

Paso 'Python'
$python = $null
foreach ($candidato in @(
        @('py', '-3.12'), @('py', '-3.13'), @('py', '-3.11'), @('python'),
        @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"), @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"))) {
    try {
        $resto = @($candidato | Select-Object -Skip 1)
        # Sin comillas dobles: Windows PowerShell 5.1 se las quita a los argumentos de programas externos.
        $v = & $candidato[0] @resto -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and "$v" -match '^\d+$' -and [int]$v -ge 311) { $python = $candidato; break }
    } catch { }
}
if (-not $python) {
    Aviso 'No encontre Python 3.11 o mas nuevo; lo instalo para tu usuario con winget.'
    try {
        winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
    } catch { throw 'No tengo winget para instalar Python. Instalalo desde python.org (marca "Add python.exe to PATH") y vuelve a ejecutar instalar.bat.' }
    $python = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    if (-not (Test-Path $python[0])) { throw 'No se pudo instalar Python. Instalalo desde python.org (marca "Add python.exe to PATH") y vuelve a ejecutar instalar.bat.' }
}
Bien "Python: $($python -join ' ')"

# ------------------------------------------------------------- 2. Entorno

# Si NOVA esta abierta, sus programas tienen tomados los archivos que vamos a reemplazar.
$abiertos = Get-Process -Name 'llama-server', 'whisper-cli', 'piper', 'pythonw', 'python' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Datos, [StringComparison]::OrdinalIgnoreCase) }
if ($abiertos) {
    Aviso 'NOVA estaba abierta; la cierro para poder actualizarla.'
    $abiertos | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Paso 'Entorno de NOVA'
$py = Join-Path $Venv 'Scripts\python.exe'
$restoPy = @($python | Select-Object -Skip 1)
if (-not (Test-Path $py)) { & $python[0] @restoPy -m venv $Venv; if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno de Python.' } }
& $py -m pip install --disable-pip-version-check -q --upgrade pip
& $py -m pip install --disable-pip-version-check -q -e "$Raiz[completo]"
if ($LASTEXITCODE -ne 0) { throw 'No se pudo instalar NOVA con pip.' }
Bien "NOVA instalada en $Venv (codigo en $Raiz; no muevas esta carpeta)"

# ---------------------------------------------------------- 3. llama.cpp

Paso 'Motor de IA (llama.cpp)'
if ($Motor -eq 'auto') {
    $Motor = 'vulkan'
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $info = (& $smi.Source --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null | Select-Object -First 1)
        if ($info) {
            Bien "Tarjeta: $info"
            $driver = [double](($info -split ',')[1].Trim() -replace '[^0-9.]', '')
            if ($driver -ge 528) { $Motor = 'cuda' } else { Aviso "Tu controlador NVIDIA ($driver) es viejo para CUDA 12; uso Vulkan. Actualizalo para ir mas rapido." }
        }
    }
}
$patron = @{ cuda = '^llama-.*-bin-win-cuda-12\.\d+-x64\.zip$'; vulkan = '^llama-.*-bin-win-vulkan-x64\.zip$'; cpu = '^llama-.*-bin-win-cpu-x64\.zip$' }[$Motor]
$llama = Ultima-Version 'ggml-org/llama.cpp' $patron
Bien "llama.cpp $($llama.tag_name), variante: $Motor"
$paquete = Archivo-De $llama $patron
if (-not $paquete) { throw "No encontre la variante $Motor de llama.cpp en la ultima version. Prueba: instalar.bat -Motor vulkan" }
$zip = Join-Path $Descargas $paquete.Nombre
Descargar $paquete.Url $zip $paquete.Sha
Extraer-Limpio $zip (Join-Path $Datos 'llama') 'llama-server.exe'
if ($Motor -eq 'cuda') {
    $cudart = Archivo-De $llama '^cudart-llama-bin-win-cuda-12\.\d+-x64\.zip$'
    if ($cudart) {
        $zipRt = Join-Path $Descargas $cudart.Nombre
        Descargar $cudart.Url $zipRt $cudart.Sha
        Expand-Archive -Path $zipRt -DestinationPath (Join-Path $Datos 'llama') -Force
    } else { Aviso 'No encontre el paquete cudart; si el motor no arranca, instala el controlador mas reciente de NVIDIA.' }
}
Bien "llama-server en $(Join-Path $Datos 'llama')"

# ---------------------------------------------------------------- 4. Modelo

if (-not $SinModelo) {
    Paso 'Modelo de lenguaje'
    $modelos = Join-Path $Datos 'models'
    New-Item -ItemType Directory -Force -Path $modelos | Out-Null
    $existentes = Get-ChildItem $modelos -Filter *.gguf -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 500MB }
    if ($existentes) {
        Bien "Ya tienes modelo: $($existentes[0].Name)"
    } else {
        $opciones = @(
            'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
            'https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
            'https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf'
        )
        $url = $opciones | Where-Object { Existe-Url $_ } | Select-Object -First 1
        if (-not $url) { throw 'No pude llegar a Hugging Face para bajar el modelo. Revisa tu conexion y vuelve a ejecutar instalar.bat.' }
        $nombre = Split-Path $url -Leaf
        Write-Host "   Modelo: $nombre (unos 2.5 GB; puede tardar)"
        Descargar $url (Join-Path $modelos $nombre) (Huella-HuggingFace $url)
    }
}

# ------------------------------------------------------------------ 4b. Vista

if (-not $SinModelo -and -not $SinVista) {
    Paso 'Vista (modelo que ve imagenes y tu pantalla)'
    try {
        $modelos = Join-Path $Datos 'models'
        New-Item -ItemType Directory -Force -Path $modelos | Out-Null
        $listo = Get-ChildItem $modelos -Filter 'mmproj-*.gguf' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($listo) {
            Bien "Ya tienes vista: $($listo.Name)"
        } else {
            $repo = $null
            $archivos = @()
            foreach ($r in @('Qwen/Qwen3-VL-4B-Instruct-GGUF', 'unsloth/Qwen3-VL-4B-Instruct-GGUF', 'ggml-org/Qwen3-VL-4B-Instruct-GGUF')) {
                try {
                    $info = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$r" -Headers $Agente -TimeoutSec 30
                    $archivos = @($info.siblings | ForEach-Object { $_.rfilename })
                    if ($archivos.Count -gt 0) { $repo = $r; break }
                } catch { }
            }
            if (-not $repo) { throw 'no encontre el modelo Qwen3-VL 4B en Hugging Face' }
            $gguf = $archivos | Where-Object { $_ -match 'Q4_K_M\.gguf$' -and $_ -notmatch 'mmproj' } | Select-Object -First 1
            $proy = $archivos | Where-Object { $_ -match 'mmproj.*Q8_0\.gguf$' } | Select-Object -First 1
            if (-not $proy) { $proy = $archivos | Where-Object { $_ -match 'mmproj.*F16\.gguf$' } | Select-Object -First 1 }
            if (-not $gguf -or -not $proy) { throw "en $repo no encontre el modelo Q4_K_M o su proyector" }
            $base = "https://huggingface.co/$repo/resolve/main"
            $nombreModelo = Split-Path $gguf -Leaf
            Write-Host "   Modelo con vista: $nombreModelo de $repo (unos 3.3 GB con su proyector; puede tardar)"
            Descargar "$base/$gguf" (Join-Path $modelos $nombreModelo) (Huella-HuggingFace "$base/$gguf")
            # El proyector se guarda con el nombre del modelo para que NOVA sepa que van juntos.
            $tipo = if ($proy -match 'Q8_0') { 'Q8_0' } else { 'F16' }
            $nombreProy = 'mmproj-' + [IO.Path]::GetFileNameWithoutExtension($nombreModelo) + "-$tipo.gguf"
            Descargar "$base/$proy" (Join-Path $modelos $nombreProy) (Huella-HuggingFace "$base/$proy")
            Bien 'NOVA ya puede ver imagenes y tu pantalla'
        }
    } catch { Aviso "No se instalo la vista: $_. NOVA igual puede leer el texto de tu pantalla (OCR de Windows)." }
}

# ------------------------------------------------------------------- 5. Voz

if (-not $SinVoz) {
    Paso 'Oido (whisper.cpp)'
    try {
        $whisper = Ultima-Version 'ggml-org/whisper.cpp' '^whisper-bin-x64\.zip$'
        $wz = Archivo-De $whisper '^whisper-bin-x64\.zip$'
        if (-not $wz) { throw 'no encontre whisper-bin-x64.zip' }
        $zip = Join-Path $Descargas "whisper-$($whisper.tag_name)-bin-x64.zip"
        Descargar $wz.Url $zip $wz.Sha
        $dirW = Join-Path $Datos 'whisper'
        if (-not (Test-Path (Join-Path $dirW 'whisper-cli.exe'))) { Extraer-Limpio $zip $dirW 'whisper-cli.exe' }
        $urlW = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin'
        Descargar $urlW (Join-Path $dirW 'ggml-small.bin') (Huella-HuggingFace $urlW)
        Bien "whisper.cpp $($whisper.tag_name) listo"
    } catch { Aviso "No se instalo el oido: $_. NOVA funciona sin el (solo texto)." }

    Paso 'Voz (Piper)'
    try {
        $dirP = Join-Path $Datos 'piper'
        $zip = Join-Path $Descargas 'piper_windows_amd64.zip'
        Descargar 'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip' $zip
        if (-not (Test-Path (Join-Path $dirP 'piper.exe'))) { Extraer-Limpio $zip $dirP 'piper.exe' }
        $voz = $null
        foreach ($v in @('es/es_MX/claude/high/es_MX-claude-high', 'es/es_MX/ald/medium/es_MX-ald-medium')) {
            $u = "https://huggingface.co/rhasspy/piper-voices/resolve/main/$v.onnx"
            if (Existe-Url $u) {
                $n = Split-Path $u -Leaf
                Descargar $u (Join-Path $dirP $n) (Huella-HuggingFace $u)
                Descargar "$u.json" (Join-Path $dirP "$n.json")
                $voz = $n
                break
            }
        }
        if (-not $voz) { throw 'no encontre una voz es_MX' }
        if ($voz -ne 'es_MX-claude-high.onnx') {
            $cfgDir = Join-Path $env:APPDATA 'NOVA'
            New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
            $cfg = Join-Path $cfgDir 'config.json'
            $actual = @{}
            if (Test-Path $cfg) { (Get-Content $cfg -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $actual[$_.Name] = $_.Value } }
            $actual['voz_tts_voz'] = $voz
            $actual | ConvertTo-Json | Set-Content $cfg -Encoding UTF8
        }
        Bien "Piper con la voz $voz"
    } catch { Aviso "No se instalo la voz: $_. NOVA funciona sin ella (solo texto)." }
}

# ------------------------------------------------------------ 6. Accesos

Paso 'Accesos directos'
$pyw = Join-Path $Venv 'Scripts\pythonw.exe'
$icono = Join-Path $Raiz 'desktop\src-tauri\icons\icon.ico'
$shell = New-Object -ComObject WScript.Shell
$inicio = Join-Path ([Environment]::GetFolderPath('Programs')) 'NOVA.lnk'
$escritorio = Join-Path ([Environment]::GetFolderPath('Desktop')) 'NOVA.lnk'
foreach ($destino in @($inicio, $escritorio)) {
    $lnk = $shell.CreateShortcut($destino)
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '-m nova servidor --abrir'
    $lnk.WorkingDirectory = $Raiz
    $lnk.IconLocation = $icono
    $lnk.Description = 'NOVA, tu asistente'
    if ($destino -eq $inicio) { $lnk.Hotkey = 'CTRL+ALT+N' }
    $lnk.Save()
}
Bien 'NOVA en el menu Inicio y en el escritorio (atajo: Ctrl+Alt+N)'

# ---------------------------------------------------------- 7. Diagnostico

Paso 'Revision del equipo (nova diagnostico)'
$env:PYTHONIOENCODING = 'utf-8'
& $py -m nova diagnostico
$fallas = $LASTEXITCODE

if (-not $SinAbrir) {
    Paso 'Abriendo NOVA'
    Start-Process -FilePath $pyw -ArgumentList '-m nova servidor --abrir' -WorkingDirectory $Raiz
}
Write-Host ''
if ($fallas -eq 0) {
    Write-Host 'Listo. NOVA esta instalada.' -ForegroundColor Green
} else {
    Write-Host 'NOVA quedo instalada, pero el diagnostico encontro cosas por arreglar (las marcadas con X).' -ForegroundColor Yellow
}
Write-Host "Reporte: $(Join-Path $Datos 'estado\diagnostico.txt')"
