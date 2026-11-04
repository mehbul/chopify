<#
.SYNOPSIS
    One-command installer for Chopify on Windows.

.DESCRIPTION
    Checks for Git, Python 3.11+, and ffmpeg, installing missing tools with
    winget when possible. Clones Chopify, creates a virtual environment,
    installs the Python dependencies, and opens Chopify Studio.

    Environment variables:
      CHOPIFY_DIR       Install folder (default: .\chopify)
      CHOPIFY_NO_START  Set to 1 to install without launching Studio
      CHOPIFY_DRY_RUN   Set to 1 to print the plan without changing anything

.EXAMPLE
    irm https://raw.githubusercontent.com/mehbul/chopify/main/install.ps1 | iex
#>

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/mehbul/chopify.git"
$Target = if ($env:CHOPIFY_DIR) {
    [System.IO.Path]::GetFullPath($env:CHOPIFY_DIR)
} else {
    Join-Path (Get-Location) "chopify"
}
$NoStart = $env:CHOPIFY_NO_START -eq "1"
$DryRun = $env:CHOPIFY_DRY_RUN -eq "1"

function Write-Step($Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Note($Message) {
    Write-Host "    $Message" -ForegroundColor DarkGray
}

function Stop-Install($Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($machine, $user) | Where-Object { $_ }
    if ($parts) {
        $env:Path = ($parts -join ";")
    }
    $wingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    if ((Test-Path $wingetLinks) -and ($env:Path -notlike "*$wingetLinks*")) {
        $env:Path = "$wingetLinks;$env:Path"
    }
}

function Install-WingetPackage($Id, $Label) {
    if (-not (Test-Command "winget")) {
        Stop-Install "$Label is missing and winget is unavailable. Install $Label manually, then rerun this command."
    }
    Write-Step "Installing $Label with winget"
    & winget install --id $Id --exact --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "winget could not install $Label. Install it manually, then rerun this command."
    }
    Update-SessionPath
}

function Get-PythonCommand {
    if (Test-Command "py") {
        foreach ($version in @("3.13", "3.12", "3.11")) {
            & py "-$version" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return @("py", "-$version")
            }
        }
    }
    if (Test-Command "python") {
        & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @("python")
        }
    }
    return $null
}

Write-Host ""
Write-Host "Chopify installer" -ForegroundColor White
Write-Host "Local video clipping, captions and exports. Nothing is uploaded."
Write-Host ""

if ($DryRun) {
    Write-Step "Dry run"
    Write-Note "Install folder: $Target"
    Write-Note "Repository:    $RepoUrl"
    Write-Note "Launch Studio: $(-not $NoStart)"
    exit 0
}

Update-SessionPath

if (-not (Test-Command "git")) {
    Install-WingetPackage "Git.Git" "Git"
}
if (-not (Test-Command "git")) {
    Stop-Install "Git is still unavailable. Close this window, open a new one, and rerun the installer."
}

$python = Get-PythonCommand
if (-not $python) {
    Install-WingetPackage "Python.Python.3.12" "Python 3.12"
    $python = Get-PythonCommand
}
if (-not $python) {
    Stop-Install "Python 3.11+ is still unavailable. Close this window, open a new one, and rerun the installer."
}

if (-not (Test-Command "ffmpeg") -or -not (Test-Command "ffprobe")) {
    Install-WingetPackage "Gyan.FFmpeg" "ffmpeg"
}
if (-not (Test-Command "ffmpeg") -or -not (Test-Command "ffprobe")) {
    Stop-Install "ffmpeg is still unavailable. Close this window, open a new one, and rerun the installer."
}

if ((Test-Path $Target) -and -not (Test-Path (Join-Path $Target "chopify.py"))) {
    if ((Get-ChildItem $Target -Force | Select-Object -First 1)) {
        Stop-Install "$Target already exists and is not a Chopify checkout. Set CHOPIFY_DIR to another folder and rerun."
    }
}

if (Test-Path (Join-Path $Target "chopify.py")) {
    Write-Step "Using existing checkout at $Target"
} else {
    Write-Step "Cloning Chopify into $Target"
    & git clone --depth 1 $RepoUrl $Target
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "Could not clone $RepoUrl."
    }
}

$venv = Join-Path $Target ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Creating the virtual environment"
    $pythonExe = $python[0]
    $pythonArgs = @()
    if ($python.Count -gt 1) {
        $pythonArgs = $python[1..($python.Count - 1)]
    }
    & $pythonExe @pythonArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        Stop-Install "Could not create the virtual environment."
    }
}

Write-Step "Installing Python dependencies"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $Target "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Stop-Install "Dependency installation failed."
}

Write-Host ""
Write-Step "Chopify is installed at $Target"

if ($NoStart) {
    Write-Note "Start Studio: $venvPython dashboard.py"
    exit 0
}

Write-Step "Starting Chopify Studio at http://127.0.0.1:8765"
Write-Note "Press Ctrl+C to stop the server."
Push-Location $Target
try {
    & $venvPython dashboard.py
} finally {
    Pop-Location
}
