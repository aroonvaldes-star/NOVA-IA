# Actualiza NOVA desde su repositorio privado de GitHub (aroonvaldes-star/NOVA-IA).
# Archivo solo en ASCII a proposito, igual que instalar.ps1.
#
# Que hace:
#   1. Cierra NOVA si esta abierta.
#   2. Baja solo lo que cambio (git). La primera vez conecta esta carpeta con el repositorio.
#   3. Si cambiaron las dependencias, las reinstala en el entorno de NOVA.
#   4. Baja el orbe libre de escritorio si hay uno nuevo y rehace los accesos directos.
#   5. Vuelve a abrir NOVA (libre en el escritorio, o en su ventana con -Ventana).
#
# Si guardaste cambios tuyos en estos archivos, no se pierden: quedan en "git stash".
#
# Opciones:
#   -SinAbrir   no abre NOVA al terminar
#   -Ventana    la abre en su ventana en vez de libre en el escritorio

param([switch]$SinAbrir, [switch]$Ventana)

$ErrorActionPreference = 'Stop'
$Repo = 'https://github.com/aroonvaldes-star/NOVA-IA.git'
$Rama = 'main'
$Raiz = $PSScriptRoot
$Datos = Join-Path $env:LOCALAPPDATA 'NOVA'
$Venv = Join-Path $Datos 'venv'

function Paso($texto) { Write-Host "`n== $texto" -ForegroundColor Cyan }
function Bien($texto) { Write-Host "   OK  $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "   !   $texto" -ForegroundColor Yellow }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Abre actualizar.bat con doble clic normal, no como administrador.' -ForegroundColor Red
    exit 1
}

# ------------------------------------------------------------------ 1. Git

Paso 'Git'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    foreach ($d in @("$env:ProgramFiles\Git\cmd", "$env:LOCALAPPDATA\Programs\Git\cmd")) {
        if (Test-Path (Join-Path $d 'git.exe')) { $env:Path = "$d;$env:Path" }
    }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Aviso 'No tienes Git; lo instalo con winget (puede pedirte permiso).'
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    $env:Path = "$env:ProgramFiles\Git\cmd;$env:LOCALAPPDATA\Programs\Git\cmd;$env:Path"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'No se pudo instalar Git. Instalalo desde git-scm.com y vuelve a abrir actualizar.bat.' }
}
Bien (git --version)

Set-Location $Raiz
if (-not (Test-Path (Join-Path $Raiz '.git'))) {
    Aviso 'Primera vez: conecto esta carpeta con el repositorio de NOVA.'
    git init -q -b $Rama
    git remote add origin $Repo
}

# ------------------------------------------------------------ 2. Cerrar NOVA

$abiertos = Get-Process -Name 'llama-server', 'whisper-cli', 'piper', 'pythonw', 'python', 'nova-escritorio' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Datos, [StringComparison]::OrdinalIgnoreCase) }
if ($abiertos) {
    Aviso 'NOVA estaba abierta; la cierro para actualizarla.'
    $abiertos | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# --------------------------------------------------------------- 3. Bajar

Paso 'Bajando cambios'
# La primera vez GitHub abre el navegador para que entres con tu cuenta (el repositorio es privado).
git fetch -q origin $Rama
if ($LASTEXITCODE -ne 0) { throw 'No pude conectarme a GitHub. Revisa tu internet y que hayas entrado con tu cuenta.' }

$antes = (git rev-parse -q --verify HEAD)
if ($antes) {
    if (git status --porcelain --untracked-files=no) {
        git stash push -q -m "cambios locales antes de actualizar $(Get-Date -Format s)"
        Aviso 'Tenias cambios en algunos archivos; los guarde con git stash.'
    }
}
git reset -q --hard "origin/$Rama"
$despues = (git rev-parse HEAD)

if ($antes -eq $despues) {
    Bien 'Ya tenias la ultima version.'
} else {
    if ($antes) { git log --format='   - %s' "$antes..$despues" } else { git log -5 --format='   - %s' }
    Bien "Version $(git log -1 --format=%h) del $(git log -1 --format=%cd --date=short)"
}

# ---------------------------------------------------------- 4. Dependencias

$py = Join-Path $Venv 'Scripts\python.exe'
$cambiaron = if ($antes -and $antes -ne $despues) { git diff --name-only $antes $despues } else { @() }
if (-not (Test-Path $py)) {
    Aviso 'NOVA aun no esta instalada en esta PC: abre instalar.bat.'
    exit 0
}
if (-not $antes -or ($cambiaron -contains 'pyproject.toml')) {
    Paso 'Dependencias'
    & $py -m pip install --disable-pip-version-check -q -e "$Raiz[completo]"
    Bien 'Listas'
}
Paso 'Orbe de escritorio'
. (Join-Path $Raiz 'escritorio.ps1')
$exeEscritorio = Obtener-Escritorio $Raiz $Datos
Crear-Accesos $Raiz $Datos $exeEscritorio
if ($exeEscritorio) { Bien 'Listo: "NOVA" la abre libre en el escritorio y "NOVA en ventana" en su ventana.' }
else { Aviso 'Todavia no hay orbe de escritorio compilado; sigo con la ventana.' }

if ($cambiaron -contains 'instalar.ps1') {
    Aviso 'Tambien cambio el instalador: cuando puedas, abre instalar.bat (lo ya descargado no se baja otra vez).'
}

# ------------------------------------------------------------- 5. Abrir

if (-not $SinAbrir) {
    Paso 'Abriendo NOVA'
    if ($exeEscritorio -and -not $Ventana) { Start-Process -FilePath $exeEscritorio -WorkingDirectory $Raiz }
    else { Start-Process -FilePath (Join-Path $Venv 'Scripts\pythonw.exe') -ArgumentList '-m nova servidor --abrir' -WorkingDirectory $Raiz }
}
Write-Host ''
Write-Host 'Listo.' -ForegroundColor Green
