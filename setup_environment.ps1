# setup_environment.ps1 — Automated Environment and Dependency Installer for PLC Tag Monitor

$ErrorActionPreference = "Stop"

Function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path    = "$machinePath;$userPath"
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " PLC Tag Monitor - System and Dependency Setup" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# -----------------------------------------------------------------
# 1. Check and Install Python
# -----------------------------------------------------------------
Write-Host "`n[1/4] Checking Python installation..." -ForegroundColor Yellow
$pythonInstalled = $false

Refresh-Path
try {
    $pyVersion = & python --version 2>&1
    if ($pyVersion -match "Python 3") {
        Write-Host "Found Python: $pyVersion" -ForegroundColor Green
        $pythonInstalled = $true
    }
} catch {
    $pythonInstalled = $false
}

if (-not $pythonInstalled) {
    try {
        $pyVersion = & py -3 --version 2>&1
        if ($pyVersion -match "Python 3") {
            Write-Host "Found Python via Launcher: $pyVersion" -ForegroundColor Green
            $pythonInstalled = $true
        }
    } catch {
        $pythonInstalled = $false
    }
}

if (-not $pythonInstalled) {
    Write-Host "Python 3 was not found on this system." -ForegroundColor Red
    Write-Host "Downloading Python 3.11.9 installer..." -ForegroundColor Cyan
    
    $pythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $pythonExe = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
    
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonExe -UseBasicParsing
    
    Write-Host "Installing Python 3.11.9 silently..." -ForegroundColor Cyan
    $process = Start-Process -FilePath $pythonExe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -Wait -PassThru
    
    if ($process.ExitCode -ne 0) {
        Write-Host "Python installation returned exit code: $($process.ExitCode)" -ForegroundColor Red
    } else {
        Write-Host "Python installation complete." -ForegroundColor Green
    }
    
    Refresh-Path
}

# -----------------------------------------------------------------
# 2. Check and Install Node.js
# -----------------------------------------------------------------
Write-Host "`n[2/4] Checking Node.js installation..." -ForegroundColor Yellow
$nodeInstalled = $false

Refresh-Path
try {
    $nodeVer = & node -v 2>&1
    if ($nodeVer -match "v") {
        Write-Host "Found Node.js: $nodeVer" -ForegroundColor Green
        $nodeInstalled = $true
    }
} catch {
    $nodeInstalled = $false
}

if (-not $nodeInstalled) {
    Write-Host "Node.js was not found on this system." -ForegroundColor Red
    Write-Host "Downloading Node.js v20.18.0 LTS installer..." -ForegroundColor Cyan
    
    $nodeUrl = "https://nodejs.org/dist/v20.18.0/node-v20.18.0-x64.msi"
    $nodeMsi = Join-Path $env:TEMP "node-v20.18.0-x64.msi"
    
    Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeMsi -UseBasicParsing
    
    Write-Host "Installing Node.js v20.18.0 silently..." -ForegroundColor Cyan
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$nodeMsi`" /qn /norestart" -Wait -PassThru
    
    if ($process.ExitCode -ne 0) {
        Write-Host "Node.js installation returned exit code: $($process.ExitCode)" -ForegroundColor Red
    } else {
        Write-Host "Node.js installation complete." -ForegroundColor Green
    }
    
    Refresh-Path
}

# -----------------------------------------------------------------
# Determine Script Paths
# -----------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Requirements.txt resolution
$reqPath = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $reqPath)) {
    $reqPath = Join-Path $ScriptDir "backend\requirements.txt"
}
if (-not (Test-Path $reqPath)) {
    $reqPath = Join-Path (Split-Path -Parent $ScriptDir) "requirements.txt"
}

# Desktop directory resolution
$desktopDir = Join-Path $ScriptDir "desktop"
if (-not (Test-Path $desktopDir)) {
    if (Test-Path (Join-Path $ScriptDir "package.json")) {
        $desktopDir = $ScriptDir
    } else {
        $desktopDir = Join-Path (Split-Path -Parent $ScriptDir) "desktop"
    }
}

# -----------------------------------------------------------------
# 3. Install Python Dependencies
# -----------------------------------------------------------------
Write-Host "`n[3/4] Installing Python modules/libraries..." -ForegroundColor Yellow
Refresh-Path

if (Test-Path $reqPath) {
    Write-Host "Using requirements file: $reqPath" -ForegroundColor Cyan
    try {
        & python -m pip install --upgrade pip setuptools 2>&1 | Write-Host
        & python -m pip install -r $reqPath 2>&1 | Write-Host
        Write-Host "Python modules successfully installed." -ForegroundColor Green
    } catch {
        Write-Host "Error installing Python requirements: $_" -ForegroundColor Red
    }
} else {
    Write-Host "Warning: requirements.txt not found at $reqPath" -ForegroundColor Red
}

# -----------------------------------------------------------------
# 4. Install Node.js Dependencies
# -----------------------------------------------------------------
Write-Host "`n[4/4] Installing Node.js packages..." -ForegroundColor Yellow
Refresh-Path

if (Test-Path $desktopDir) {
    Write-Host "Installing npm packages in: $desktopDir" -ForegroundColor Cyan
    try {
        Push-Location $desktopDir
        & npm install 2>&1 | Write-Host
        Pop-Location
        Write-Host "Node packages successfully installed." -ForegroundColor Green
    } catch {
        Write-Host "Error installing Node packages: $_" -ForegroundColor Red
        Pop-Location
    }
} else {
    Write-Host "Warning: desktop directory not found at $desktopDir" -ForegroundColor Red
}

Write-Host "`n==================================================" -ForegroundColor Cyan
Write-Host " Setup Completed Successfully!" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
