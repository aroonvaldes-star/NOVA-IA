# OmniRoute para NOVA: un enrutador de cerebros grandes que corre en tu propia PC, sin administrador.
# Archivo solo en ASCII a proposito, igual que instalar.ps1.
#
# Que hace:
#   1. Baja Node 24 (oficial, verificado con SHA256) a %LOCALAPPDATA%\NOVA\node.
#   2. Instala OmniRoute (version fija) en %LOCALAPPDATA%\NOVA\omniroute con sus datos en
#      %LOCALAPPDATA%\NOVA\omniroute-datos.
#   3. Lo deja escuchando SOLO en 127.0.0.1 (nadie de tu red puede usar tus cuotas), con secretos
#      propios, contrasena del panel al azar y una clave solo para NOVA.
#   4. Si NOVA ya usaba Groq, le pasa esa clave a OmniRoute. Te deja agregar otras claves gratis
#      (se escriben aqui, nunca en un chat).
#   5. Apunta el cerebro grande de NOVA a OmniRoute con el modelo "auto": si un proveedor se agota,
#      OmniRoute cambia solo a otro, y NOVA anota en su auditoria quien contesto cada vez.
#
# Puedes volver a ejecutarlo: lo que ya esta no se baja otra vez y tus claves se conservan.
#
# Opciones:
#   -SinPreguntas   no pide claves (para pruebas)

param([switch]$SinPreguntas)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$VersionOmni = '3.8.52'
$Puerto = 20128
$Datos = Join-Path $env:LOCALAPPDATA 'NOVA'
$NodeDir = Join-Path $Datos 'node'
$Omni = Join-Path $Datos 'omniroute'
$OmniDatos = Join-Path $Datos 'omniroute-datos'
$Estado = Join-Path $Datos 'estado'
$Descargas = Join-Path $Datos 'descargas'
$ConfigNova = Join-Path $env:APPDATA 'NOVA'
$Agente = @{ 'User-Agent' = 'NOVA-instalador' }
# Paquetes de OmniRoute que necesitan correr su paso de instalacion (lista de su README para la 3.8.52).
$PermitirScripts = 'omniroute,keytar,onnxruntime-node,@parcel/watcher,@swc/core,protobufjs,esbuild'

function Paso($texto) { Write-Host "`n== $texto  ($(Get-Date -Format HH:mm:ss))" -ForegroundColor Cyan }
function Bien($texto) { Write-Host "   OK  $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "   !   $texto" -ForegroundColor Yellow }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
# Las maquinas de prueba de GitHub siempre son administrador; en tu PC nunca se acepta.
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -and $env:GITHUB_ACTIONS -ne 'true') {
    Write-Host 'OmniRoute se instala con tu usuario normal, no como administrador.' -ForegroundColor Red
    Write-Host 'Cierra esta ventana y vuelve a abrir omniroute.bat con doble clic.'
    exit 1
}
New-Item -ItemType Directory -Force -Path $Datos, $OmniDatos, $Estado, $Descargas, $ConfigNova | Out-Null

function Descargar($Url, $Destino, $Sha256) {
    if ((Test-Path $Destino) -and (Get-FileHash $Destino -Algorithm SHA256).Hash.ToLower() -eq $Sha256) { return }
    $parcial = "$Destino.parcial"
    Write-Host "   ... descargando $(Split-Path $Destino -Leaf)"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -L --fail --retry 3 --retry-delay 3 -s -S -A 'NOVA-instalador' -o $parcial $Url
        if ($LASTEXITCODE -ne 0) { throw "No pude descargar $Url (curl salio con $LASTEXITCODE)" }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $parcial -Headers $Agente -UseBasicParsing
    }
    if ((Get-FileHash $parcial -Algorithm SHA256).Hash.ToLower() -ne $Sha256) {
        Remove-Item $parcial -Force
        throw "La huella SHA256 de $(Split-Path $Destino -Leaf) no coincide; lo borre por seguridad."
    }
    Move-Item $parcial $Destino -Force
}

function Azar($bytes) {
    $b = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return $b
}

function Leer-Env($ruta) {
    $valores = [ordered]@{}
    if (Test-Path $ruta) {
        foreach ($l in [IO.File]::ReadAllLines($ruta)) {
            if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { $valores[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $valores
}

function Guardar-Env($ruta, $valores) {
    $lineas = foreach ($k in $valores.Keys) { "$k=$($valores[$k])" }
    [IO.File]::WriteAllText($ruta, (($lineas -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding $false))
}

function Escuchando {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $Puerto); return $true } catch { return $false } finally { $c.Close() }
}

# El servidor que arranca este script (para apagarlo con todo y sus procesos hijos).
$script:Servidor = $null

function Arrancar-Omni {
    if (Escuchando) { return }
    $log = Join-Path $Estado 'omniroute.log'
    $err = Join-Path $Estado 'omniroute-errores.log'
    $script:Servidor = Start-Process -FilePath $Node -ArgumentList @("`"$Cli`"", 'serve', '--no-open', '--no-tray') `
        -WorkingDirectory $Omni -WindowStyle Hidden -PassThru -RedirectStandardOutput $log -RedirectStandardError $err
    for ($i = 0; $i -lt 180; $i++) {
        if (Escuchando) { return }
        if ($script:Servidor.HasExited) { break }
        Start-Sleep -Seconds 1
    }
    $cola = @()
    foreach ($f in $log, $err) { if (Test-Path $f) { $cola += Get-Content $f -Tail 15 } }
    throw ("OmniRoute no abrio el puerto $Puerto. Ultimas lineas:`n" + ($cola -join "`n"))
}

function Apagar-Omni {
    if ($script:Servidor -and -not $script:Servidor.HasExited) {
        & taskkill.exe /T /F /PID $script:Servidor.Id 2>&1 | Out-Null
    }
    $script:Servidor = $null
    for ($i = 0; $i -lt 20 -and (Escuchando); $i++) { Start-Sleep -Seconds 1 }
}

function Omni([string[]]$argumentos, $entrada = $null) {
    # Corre la linea de comandos de OmniRoute; con $entrada se la pasa por stdin (asi las claves no
    # quedan en la lista de procesos).
    $p = New-Object Diagnostics.Process
    $p.StartInfo.FileName = $Node
    $p.StartInfo.Arguments = "`"$Cli`" " + (($argumentos | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join ' ')
    $p.StartInfo.WorkingDirectory = $Omni
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.RedirectStandardInput = $true
    $p.StartInfo.RedirectStandardOutput = $true
    $p.StartInfo.RedirectStandardError = $true
    $p.StartInfo.CreateNoWindow = $true
    [void]$p.Start()
    if ($null -ne $entrada) { $p.StandardInput.Write($entrada) }
    $p.StandardInput.Close()
    $salida = $p.StandardOutput.ReadToEndAsync()
    $errores = $p.StandardError.ReadToEndAsync()
    $p.WaitForExit()
    return @{ Codigo = $p.ExitCode; Salida = $salida.Result; Errores = $errores.Result }
}

function Omni-Api([string[]]$comando, $json) {
    # Llama a su API de administracion (en esta PC basta la credencial local de su linea de comandos).
    $cuerpo = Join-Path $Descargas 'omniroute-cuerpo.json'
    [IO.File]::WriteAllText($cuerpo, $json, (New-Object Text.UTF8Encoding $false))
    try { return Omni (@('api') + $comando + @('--body', "@$cuerpo")) } finally { Remove-Item $cuerpo -Force -ErrorAction SilentlyContinue }
}

function Sin-Ruido($r) {
    $t = (($r.Salida + "`n" + $r.Errores) -split "`n") | Where-Object { $_.Trim() -and $_ -notmatch 'Loaded env from|is ignored' }
    $t = ($t -join ' ').Trim()
    return $t.Substring(0, [Math]::Min(400, $t.Length))
}

# ------------------------------------------------------------------ 1. Node

Paso 'Node 24 (solo para OmniRoute)'
$Node = Join-Path $NodeDir 'node.exe'
$versionNode = if (Test-Path $Node) { (& $Node --version) } else { '' }
if ($versionNode -notmatch '^v24\.') {
    $base = 'https://nodejs.org/dist/latest-v24.x'
    $sumas = (Invoke-WebRequest -Uri "$base/SHASUMS256.txt" -Headers $Agente -UseBasicParsing).Content
    if ($sumas -is [byte[]]) { $sumas = [Text.Encoding]::ASCII.GetString($sumas) }
    $linea = ($sumas -split "`n") | Where-Object { $_ -match '^([0-9a-f]{64})\s+(node-v24\.[0-9.]+-win-x64\.zip)\s*$' } | Select-Object -First 1
    if (-not $linea) { throw 'No encontre Node 24 para Windows en nodejs.org' }
    $null = $linea -match '^([0-9a-f]{64})\s+(\S+)'
    $sha = $Matches[1]; $zipNombre = $Matches[2]
    $zip = Join-Path $Descargas $zipNombre
    Descargar "$base/$zipNombre" $zip $sha
    $tmp = "$NodeDir.nuevo"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $dentro = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (Test-Path $NodeDir) { Remove-Item $NodeDir -Recurse -Force }
    Move-Item $dentro.FullName $NodeDir
    Remove-Item $tmp -Recurse -Force
    $versionNode = (& $Node --version)
}
Bien "Node $versionNode en $NodeDir"
$env:Path = "$NodeDir;$env:Path"
$Npm = Join-Path $NodeDir 'node_modules\npm\bin\npm-cli.js'

# ------------------------------------------------------------- 2. OmniRoute

Paso "OmniRoute $VersionOmni"
$Cli = Join-Path $Omni 'node_modules\omniroute\bin\omniroute.mjs'
$paquete = Join-Path $Omni 'node_modules\omniroute\package.json'
$instalada = if (Test-Path $paquete) { (Get-Content $paquete -Raw | ConvertFrom-Json).version } else { '' }
if ($instalada -ne $VersionOmni) {
    # Si NOVA ya lo tenia prendido, lo apago para poder reemplazar sus archivos.
    Get-Process -Name 'node' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($NodeDir, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host '   ... instalando (pesa unos 500 MB; la primera vez tarda de 10 a 30 minutos, no cierres esta ventana)'
    # Sin -Wait: ese espera tambien a los procesos que deje npm vivos y se puede quedar colgado.
    $npmLog = Join-Path $Estado 'omniroute-npm.log'
    $npmErr = Join-Path $Estado 'omniroute-npm-errores.log'
    $r = Start-Process -FilePath $Node -PassThru -NoNewWindow -WorkingDirectory $Datos `
        -RedirectStandardOutput $npmLog -RedirectStandardError $npmErr -ArgumentList @(
        "`"$Npm`"", 'install', '-g', '--prefix', "`"$Omni`"", '--no-fund', '--no-audit', '--loglevel', 'warn',
        "--allow-scripts=$PermitirScripts", "omniroute@$VersionOmni")
    $null = $r.Handle  # sin esto Windows PowerShell a veces no da el codigo de salida
    $r.WaitForExit()
    if ($r.ExitCode -ne 0 -or -not (Test-Path $Cli)) {
        $cola = @(); foreach ($f in $npmLog, $npmErr) { if (Test-Path $f) { $cola += Get-Content $f -Tail 20 } }
        throw ("npm no pudo instalar OmniRoute (salio con $($r.ExitCode)). Ultimas lineas:`n" + ($cola -join "`n"))
    }
}
Bien "OmniRoute $VersionOmni en $Omni"

# ------------------------------------------------------- 3. Configuracion segura

Paso 'Configuracion (solo en esta PC)'
$env:DATA_DIR = $OmniDatos
$env:OMNIROUTE_SERVER_HOST = '127.0.0.1'
$env:PORT = "$Puerto"
$env:OMNIROUTE_NO_UPDATE_NOTIFIER = '1'
# Su linea de comandos usa "localhost" y en Windows eso puede ser ::1, donde OmniRoute no escucha.
$env:OMNIROUTE_BASE_URL = "http://127.0.0.1:$Puerto"
Remove-Item Env:HOSTNAME -ErrorAction SilentlyContinue

$archivoEnv = Join-Path $OmniDatos '.env'
$envOmni = Leer-Env $archivoEnv
$nuevo = -not $envOmni.Contains('INITIAL_PASSWORD')
$envOmni['OMNIROUTE_SERVER_HOST'] = '127.0.0.1'
$envOmni['PORT'] = "$Puerto"
$envOmni['OMNIROUTE_NO_UPDATE_NOTIFIER'] = '1'
if (-not $envOmni['JWT_SECRET']) { $envOmni['JWT_SECRET'] = [Convert]::ToBase64String((Azar 48)) }
if (-not $envOmni['API_KEY_SECRET']) { $envOmni['API_KEY_SECRET'] = -join ((Azar 32) | ForEach-Object { $_.ToString('x2') }) }
if ($nuevo) {
    $letras = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $envOmni['INITIAL_PASSWORD'] = -join ((Azar 16) | ForEach-Object { $letras[$_ % $letras.Length] })
}
if (-not $envOmni.Contains('REQUIRE_API_KEY')) { $envOmni['REQUIRE_API_KEY'] = 'false' }
Guardar-Env $archivoEnv $envOmni
$contrasenaPanel = $envOmni['INITIAL_PASSWORD']
Bien 'Escucha solo en 127.0.0.1, con secretos propios'

# ------------------------------------------------------- 4. Config de NOVA

$archivoNova = $null
foreach ($n in 'config.json', 'config.json.txt', 'config.txt') {
    $f = Join-Path $ConfigNova $n
    if (Test-Path $f) { $archivoNova = $f; break }
}
if (-not $archivoNova) { $archivoNova = Join-Path $ConfigNova 'config.json' }
$cfg = $null
if (Test-Path $archivoNova) {
    $texto = [IO.File]::ReadAllText($archivoNova).Trim([char]0xFEFF)
    if ($texto.Trim()) {
        try { $cfg = $texto | ConvertFrom-Json }
        catch { throw "Tu $archivoNova tiene un error y no lo quiero romper mas. Corrigelo (o borralo) y vuelve a abrir omniroute.bat." }
    }
}
if (-not $cfg) { $cfg = New-Object PSObject }
function Valor($nombre) { if ($cfg.PSObject.Properties.Name -contains $nombre) { return [string]$cfg.$nombre }; return '' }

# ------------------------------------------------------- 5. Clave para NOVA

Paso 'Clave para que solo NOVA lo use'
$claveNova = Valor 'cerebro_clave'
$yaApuntaba = (Valor 'cerebro_endpoint') -match "^http://(127\.0\.0\.1|localhost):$Puerto/v1/?$"
if (-not ($yaApuntaba -and $claveNova -and $envOmni['REQUIRE_API_KEY'] -eq 'true')) {
    $envOmni['REQUIRE_API_KEY'] = 'false'
    Guardar-Env $archivoEnv $envOmni
    Apagar-Omni
    Arrancar-Omni
    $r = Omni-Api @('api-keys', 'post-api-keys') '{"name":"NOVA"}'
    $claveNova = $null
    if ($r.Salida -match '"(?:key|apiKey|value|token)"\s*:\s*"([^"]{16,})"') { $claveNova = $Matches[1] }
    if ($claveNova) {
        $envOmni['REQUIRE_API_KEY'] = 'true'
        Guardar-Env $archivoEnv $envOmni
        Bien 'Creada; OmniRoute ya no contesta a nadie sin ella'
    } else {
        Aviso 'No pude crear la clave automaticamente; OmniRoute queda abierto solo para esta PC.'
        Aviso ("Respuesta: " + (Sin-Ruido $r))
        $claveNova = ''
    }
    Apagar-Omni
} else {
    Bien 'Ya la tenia'
}

# ------------------------------------------------------- 6. Proveedores gratis

Paso 'Proveedores'
$endpointAntes = Valor 'cerebro_endpoint'
if ($endpointAntes -match 'api\.groq\.com' -and (Valor 'cerebro_clave')) {
    $r = Omni @('keys', 'add', 'groq', '--stdin') (Valor 'cerebro_clave')
    if ($r.Codigo -eq 0) { Bien 'Pase tu clave de Groq a OmniRoute' } else { Aviso "No pude pasar tu clave de Groq: $(Sin-Ruido $r)" }
}
if (-not $SinPreguntas) {
    Write-Host ''
    Write-Host '   Puedes darle mas proveedores gratis: cuando uno se agota, OmniRoute usa otro.'
    Write-Host '   Recomendados: groq, cerebras, openrouter, gemini. Las claves se escriben solo aqui; nunca las pegues en un chat.'
    while ($true) {
        $prov = (Read-Host '   Proveedor (Enter para terminar)').Trim().ToLower()
        if (-not $prov) { break }
        $segura = Read-Host "   Clave de $prov (no se ve al escribir)" -AsSecureString
        $plano = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura))
        if (-not $plano) { continue }
        $r = Omni @('keys', 'add', $prov, '--stdin') $plano
        $plano = $null
        if ($r.Codigo -eq 0) { Bien "Agregue $prov" } else { Aviso "No pude agregar ${prov}: $(Sin-Ruido $r)" }
    }
}
$lista = Omni @('keys', 'list')
Write-Host ($lista.Salida.Trim() -replace '(?m)^', '   ')

# ------------------------------------------------------- 7. NOVA apunta a OmniRoute

Paso 'NOVA usa OmniRoute como cerebro grande'
if ((Test-Path $archivoNova) -and -not $yaApuntaba) { Copy-Item $archivoNova "$archivoNova.antes-omniroute" -Force }
foreach ($par in @(
        @('cerebro_endpoint', "http://127.0.0.1:$Puerto/v1"),
        @('cerebro_clave', $claveNova),
        @('cerebro_api_modelo', 'auto'),
        @('cerebro_modelo', 'OmniRoute (auto)'),
        @('cerebro_auto', $true))) {
    $cfg | Add-Member -NotePropertyName $par[0] -NotePropertyValue $par[1] -Force
}
[IO.File]::WriteAllText($archivoNova, ($cfg | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
Bien "Listo en $archivoNova (la anterior quedo en .antes-omniroute)"

Arrancar-Omni

# El modelo "auto" tambien usaria servicios sin clave (paginas de prueba, navegadores automaticos,
# CLIs de otros). NOVA solo debe usar los proveedores a los que tu les diste clave oficial.
Paso 'Solo tus proveedores'
$sinClave = '["devin-cli-agentic","duckduckgo-web","cloudflare-playground","veoaifree-web","auggie","zcode","codex-app-server","uncloseai","aihorde"]'
$r = Omni-Api @('settings', 'patch-api-settings') ('{"blockedProviders":' + $sinClave + ',"excludeTosAvoid":true}')
if ($r.Codigo -eq 0 -and $r.Salida -notmatch '"error"') { Bien 'Nada de servicios sin clave ni de los que prohiben este uso' }
else { Aviso "No pude ajustarlo: $(Sin-Ruido $r)" }

$archivoPanel = Join-Path $OmniDatos 'contrasena-del-panel.txt'
if ($nuevo) { [IO.File]::WriteAllText($archivoPanel, "Panel de OmniRoute: http://127.0.0.1:$Puerto`r`nContrasena inicial: $contrasenaPanel`r`n") }
Write-Host ''
Write-Host "Listo. Panel de OmniRoute (auditoria completa, cuotas, proveedores): http://127.0.0.1:$Puerto" -ForegroundColor Green
if (Test-Path $archivoPanel) { Write-Host "La contrasena del panel esta en $archivoPanel (cambiala ahi en Settings > Security)." }
Write-Host 'NOVA lo prende sola cada vez que abre. Preguntale "quien contesto" para ver que modelo respondio.'
