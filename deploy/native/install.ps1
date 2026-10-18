<#
.SYNOPSIS
Install the ECC Challenges worker on Windows (x64 or ARM64) without containers.

.DESCRIPTION
Builds the engine with CMake and the Visual Studio C++ compiler, runs its
self-test, installs the client and writes bin\ecc-worker.cmd. A CUDA engine is
built as well when the CUDA toolkit is installed (x64 only).

Needs Python 3.10+ (python.org or the py launcher), CMake 3.21+ and Visual
Studio 2022 or its Build Tools with the "Desktop development with C++"
workload. Linux and macOS use deploy/native/install.sh.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\native\install.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\native\install.ps1 -Prefix D:\ecc -Cuda Off
#>
param(
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'ecc-challenges'),
    [ValidateSet('Auto', 'On', 'Off')][string]$Cuda = 'Auto'
)
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw 'need CMake 3.21+ on PATH (it ships with the Visual Studio C++ workload)'
}

# Resolve one real interpreter; the wrapper runs it by absolute path. The
# Microsoft Store "python" alias fails this check and is skipped.
# The check avoids double quotes, which Windows PowerShell 5.1 does not pass
# through to native programs intact.
$python = $null
foreach ($candidate in 'py -3', 'python', 'python3') {
    $parts = $candidate -split ' '
    if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
    $launcherArgs = @($parts | Select-Object -Skip 1)
    try {
        $found = & $parts[0] @launcherArgs -c 'import sys; print(sys.executable); sys.exit(sys.version_info < (3, 10))' 2>$null
    } catch { continue }
    if ($LASTEXITCODE -eq 0 -and $found) { $python = "$found".Trim(); break }
}
if (-not $python) { throw 'need Python 3.10 or newer from python.org' }

# Build for the machine's own architecture, also from an emulated x64 shell.
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$configure = @('-S', "$repo\worker", '-B')
$build = Join-Path ([IO.Path]::GetTempPath()) ('ecc-engine-' + [guid]::NewGuid())
$configure += @($build, "-DECC_ENABLE_CUDA=$($Cuda.ToUpper())")
if (-not $env:CMAKE_GENERATOR) {
    $platform = @{ 'AMD64' = 'x64'; 'ARM64' = 'ARM64' }[$arch]
    if ($platform) { $configure += @('-A', $platform) }
}

Write-Host "building the engine for $arch"
try {
    Invoke-Checked cmake $configure
    Invoke-Checked cmake @('--build', $build, '--config', 'Release', '--parallel')
    Invoke-Checked cmake @('--install', $build, '--config', 'Release', '--prefix', $Prefix)
} finally {
    Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
}
$bin = Join-Path $Prefix 'bin'
Invoke-Checked "$bin\generic-engine.exe" @('--self-test')
$cudaEngine = Join-Path $bin 'generic-engine-cuda.exe'
if (Test-Path $cudaEngine) {
    # As on Linux, the CUDA build also carries the CPU backend.
    Invoke-Checked $cudaEngine @('--self-test')
    Move-Item -Force $cudaEngine "$bin\generic-engine.exe"
}
Invoke-Checked "$bin\generic-engine.exe" @('--probe')

Write-Host "installing client to $Prefix"
$lib = Join-Path $Prefix 'lib'
if (Test-Path $lib) { Remove-Item -Recurse -Force $lib }
New-Item -ItemType Directory -Force (Join-Path $lib 'ecc_challenges') | Out-Null
Copy-Item "$repo\worker\client.py", "$repo\worker\protocol.py" $lib
Copy-Item -Recurse "$repo\worker\generic" (Join-Path $lib 'generic')
Copy-Item "$repo\src\ecc_challenges\__init__.py" (Join-Path $lib 'ecc_challenges')
Copy-Item -Recurse "$repo\src\ecc_challenges\multicurve" (Join-Path $lib 'ecc_challenges\multicurve')
Get-ChildItem $lib -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

$wrapper = @"
@echo off
rem Settings come from the environment; see docs\running-a-worker.md.
setlocal
if not defined ECC_GENERIC_ENGINE set "ECC_GENERIC_ENGINE=$bin\generic-engine.exe"
if not defined ECC_PROTOCOL set "ECC_PROTOCOL=2"
if not defined ECC_API_URL set "ECC_API_URL=https://ecc-challenges.com"
if not defined ECC_API_KEY if not defined ECC_API_KEY_FILE set "ECC_API_KEY_FILE=%USERPROFILE%\.config\ecc-challenges\api-key"
"$python" -B "$lib\client.py" %*
"@
# cmd.exe reads batch files in the console (OEM) code page.
try {
    $encoding = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
} catch {
    $encoding = [Text.Encoding]::ASCII
}
[IO.File]::WriteAllText((Join-Path $bin 'ecc-worker.cmd'), $wrapper, $encoding)

Write-Host @"

Installed to $Prefix. Run a worker in a console with:
  set ECC_CAMPAIGN=eccp-131
  set ECC_STATE_DIR=%USERPROFILE%\ecc-data\eccp-131
  "$bin\ecc-worker.cmd"
Stop it with Ctrl-C: the worker finishes its round, uploads and saves.
"@
