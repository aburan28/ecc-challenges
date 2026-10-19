# Download, build and start the ECC Challenges worker on Windows (x64 or ARM64).
#
#   irm https://ecc-challenges.com/install.ps1 | iex
#
# Installs to %LOCALAPPDATA%\ecc-challenges, asks for your API key once (saved
# to %USERPROFILE%\.config\ecc-challenges\api-key, readable only by you) and
# offers to start a worker. Run it again to update. Settings, as environment
# variables: ECC_CAMPAIGN (default eccp-131), ECC_STATE_DIR, ECC_REF (git ref,
# default tested release), ECC_START=no. Needs Python 3.10+, CMake and the Visual Studio
# 2022 C++ build tools; this script prints the winget commands if one is
# missing. Source: deploy\native\install.ps1 at
# https://github.com/aburan28/ecc-challenges
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$repo = 'aburan28/ecc-challenges'
$api = if ($env:ECC_API_URL) { $env:ECC_API_URL } else { 'https://ecc-challenges.com' }
$ref = if ($env:ECC_REF) { $env:ECC_REF } else { '57429625caa4c2e885a3fb79dd5eff66fea0e55e' }
$campaign = if ($env:ECC_CAMPAIGN) { $env:ECC_CAMPAIGN } else { 'eccp-131' }
$state = if ($env:ECC_STATE_DIR) { $env:ECC_STATE_DIR } else { Join-Path $env:USERPROFILE "ecc-data\$campaign" }
$prefix = Join-Path $env:LOCALAPPDATA 'ecc-challenges'
$keyFile = Join-Path $env:USERPROFILE '.config\ecc-challenges\api-key'

function Say([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

# The real check (Python version, compiler) is in deploy\native\install.ps1;
# this only catches the common first-run gaps and says how to fill them.
$missing = @()
$hasPython = (Get-Command py -ErrorAction SilentlyContinue) -or
    ((Get-Command python -ErrorAction SilentlyContinue) -and
     (Get-Command python).Source -notlike '*\WindowsApps\*')
if (-not $hasPython) { $missing += 'Python 3.10+' }
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) { $missing += 'CMake' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$hasVc = (Test-Path $vswhere) -and
    (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 Microsoft.VisualStudio.Component.VC.Tools.ARM64 -requiresAny -property installationPath)
if (-not $hasVc) { $missing += 'Visual Studio C++ build tools' }
if ($missing) {
    Write-Host "Missing: $($missing -join ', '). Install them with:" -ForegroundColor Yellow
    Write-Host '  winget install -e --id Python.Python.3.12'
    Write-Host '  winget install -e --id Kitware.CMake'
    Write-Host '  winget install -e --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"'
    Write-Host 'then open a new PowerShell window and run this command again.'
    return
}

$work = Join-Path ([IO.Path]::GetTempPath()) ('ecc-install-' + [guid]::NewGuid())
New-Item -ItemType Directory $work | Out-Null
try {
    Say "downloading $repo ($ref)"
    $zip = Join-Path $work 'source.zip'
    Invoke-WebRequest -UseBasicParsing "https://codeload.github.com/$repo/zip/$ref" -OutFile $zip
    Expand-Archive $zip -DestinationPath $work
    $source = (Get-ChildItem $work -Directory | Select-Object -First 1).FullName

    Say "building the worker into $prefix (a few minutes)"
    $log = Join-Path $work 'build.log'
    # Windows PowerShell 5.1 turns redirected native stderr into errors, which
    # 'Stop' would make fatal; the exit code decides instead.
    $ErrorActionPreference = 'Continue'
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$source\deploy\native\install.ps1" -Prefix $prefix *> $log
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) {
        Get-Content $log -Tail 30 | Write-Host
        throw "build failed; the log is above. Please open an issue at https://github.com/$repo/issues"
    }
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

if (-not (Test-Path $keyFile) -or -not (Get-Item $keyFile).Length) {
    Say "paste the API key from $api/dashboard (input is hidden), then press Enter"
    $secure = Read-Host -AsSecureString 'API key'
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
    if ($key -notlike 'ecc1_*') { throw "that doesn't look like an API key (they start with ecc1_)" }
    New-Item -ItemType Directory -Force (Split-Path $keyFile) | Out-Null
    [IO.File]::WriteAllText($keyFile, "$key`n", [Text.Encoding]::ASCII)
    & icacls $keyFile /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null
    Say "saved the key to $keyFile"
}

New-Item -ItemType Directory -Force $state | Out-Null
$worker = Join-Path $prefix 'bin\ecc-worker.cmd'
Write-Host @"

Installed. Start a worker any time in PowerShell with:
  `$env:ECC_CAMPAIGN = '$campaign'; `$env:ECC_STATE_DIR = '$state'; & '$worker'
Stop it with Ctrl-C: it finishes its round, uploads and saves. Keep
$state; the run resumes from it.
"@

$enabled = $false
try { $enabled = (Invoke-RestMethod -UseBasicParsing "$api/v1/campaigns").multicurve_enabled } catch {}
if (-not $enabled) {
    Write-Host @"

Note: $api is not yet accepting native workers (all-curves release is off;
see $api/v1/campaigns). Start the command above once it is on.
"@ -ForegroundColor Yellow
    return
}
if ($env:ECC_START -eq 'no') { return }
$answer = Read-Host 'Start the worker now? [Y/n]'
if ($answer -match '^[nN]') { return }
$env:ECC_CAMPAIGN = $campaign
$env:ECC_STATE_DIR = $state
& $worker
}
