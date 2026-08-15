param(
    [string]$VenvPath = "C:\Users\b\AppData\Local\Temp\jarvis-r4-venv",
    [string]$HostPython = "C:\Python313\python.exe",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")),
    [string]$LockFile = "",
    [switch]$UseCache,
    [string]$CacheDir = ""
)

$ErrorActionPreference = "Stop"
if (-not $LockFile) { $LockFile = Join-Path $RepoRoot "requirements-dev.txt" }

if (-not (Test-Path -LiteralPath $HostPython)) { throw "Host Python not found: $HostPython" }
if (-not (Test-Path -LiteralPath $LockFile)) { throw "Lock file not found: $LockFile" }
if (Test-Path -LiteralPath $VenvPath) { Remove-Item -LiteralPath $VenvPath -Recurse -Force }

& $HostPython -B -m venv --without-pip --system-site-packages $VenvPath
if ($LASTEXITCODE -ne 0) { throw "venv creation failed with exit $LASTEXITCODE" }

$SitePackages = Join-Path $VenvPath "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null
$SrcPath = Join-Path $RepoRoot "src"
[System.IO.File]::WriteAllText(
    (Join-Path $SitePackages "r4_remaster_src.pth"),
    $SrcPath + "`r`n",
    (New-Object System.Text.UTF8Encoding($false))
)

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$PipArgs = @("--python", $VenvPython, "install", "-r", $LockFile)
if ($UseCache) {
    if (-not (Test-Path -LiteralPath $CacheDir)) { throw "Cache directory not found: $CacheDir" }
    $PipArgs += @("--no-index", "--find-links", $CacheDir)
}

& $HostPython -m pip @PipArgs
if ($LASTEXITCODE -ne 0) { throw "pinned install failed with exit $LASTEXITCODE" }

Write-Host "isolated venv ready: $VenvPath"
& $VenvPython -c "import sys; print('python', sys.version.split()[0])"
