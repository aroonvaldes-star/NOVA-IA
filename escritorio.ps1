# Funciones compartidas por instalar.ps1 y actualizar.ps1: el orbe de escritorio y los accesos directos.
# Archivo solo en ASCII a proposito.
#
# El orbe libre (nova-escritorio.exe) lo compila GitHub y lo deja en la rama "escritorio"
# del repositorio. Aqui se baja con git a %LOCALAPPDATA%\NOVA\escritorio.

function Obtener-Escritorio($Raiz, $Datos) {
    $dir = Join-Path $Datos 'escritorio'
    $exe = Join-Path $dir 'nova-escritorio.exe'
    $hayExe = Test-Path $exe
    if (-not (Test-Path (Join-Path $Raiz '.git')) -or -not (Get-Command git -ErrorAction SilentlyContinue)) {
        if ($hayExe) { return $exe } else { return $null }
    }
    $antes = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        Push-Location $Raiz
        git fetch -q origin escritorio 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            if ($hayExe) { return $exe } else { return $null }
        }
        $remota = ((git cat-file blob origin/escritorio:VERSION) | Out-String).Trim()
        $marca = Join-Path $dir 'VERSION'
        $local = if (Test-Path $marca) { (Get-Content $marca -Raw).Trim() } else { '' }
        if ($hayExe -and $remota -eq $local) { return $exe }

        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Get-Process -Name 'nova-escritorio' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        # El .exe se copia byte por byte: las tuberias de PowerShell 5.1 lo corromperian.
        $tmp = "$exe.nuevo"
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = (Get-Command git).Source
        $psi.Arguments = 'cat-file blob origin/escritorio:nova-escritorio.exe'
        $psi.WorkingDirectory = $Raiz
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.CreateNoWindow = $true
        $p = [Diagnostics.Process]::Start($psi)
        $fs = [IO.File]::Create($tmp)
        $p.StandardOutput.BaseStream.CopyTo($fs)
        $fs.Close()
        $p.WaitForExit()
        if ($p.ExitCode -ne 0 -or (Get-Item $tmp).Length -lt 100000) {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            if ($hayExe) { return $exe } else { return $null }
        }
        Move-Item $tmp $exe -Force
        Set-Content -Path $marca -Value $remota -Encoding ASCII
        return $exe
    } catch {
        if (Test-Path $exe) { return $exe } else { return $null }
    } finally {
        Pop-Location
        $ErrorActionPreference = $antes
    }
}

function Crear-Accesos($Raiz, $Datos, $Exe) {
    $pyw = Join-Path $Datos 'venv\Scripts\pythonw.exe'
    $icono = Join-Path $Raiz 'desktop\src-tauri\icons\icon.ico'
    $shell = New-Object -ComObject WScript.Shell
    $programas = [Environment]::GetFolderPath('Programs')
    $escritorio = [Environment]::GetFolderPath('Desktop')

    function Acceso($Destino, $Programa, $Argumentos, $Descripcion, $Atajo) {
        $lnk = $shell.CreateShortcut($Destino)
        $lnk.TargetPath = $Programa
        $lnk.Arguments = $Argumentos
        $lnk.WorkingDirectory = $Raiz
        $lnk.IconLocation = $icono
        $lnk.Description = $Descripcion
        $lnk.Hotkey = $Atajo
        $lnk.Save()
    }

    if ($Exe) {
        # "NOVA": el orbe libre sobre el escritorio. "NOVA en ventana": la ventana de siempre.
        foreach ($d in @($programas, $escritorio)) {
            Acceso (Join-Path $d 'NOVA.lnk') $Exe '' 'NOVA libre en tu escritorio' $(if ($d -eq $programas) { 'CTRL+ALT+N' } else { '' })
            Acceso (Join-Path $d 'NOVA en ventana.lnk') $pyw '-m nova servidor --abrir' 'NOVA en su ventana' ''
        }
    } else {
        foreach ($d in @($programas, $escritorio)) {
            Acceso (Join-Path $d 'NOVA.lnk') $pyw '-m nova servidor --abrir' 'NOVA, tu asistente' $(if ($d -eq $programas) { 'CTRL+ALT+N' } else { '' })
        }
    }
}
