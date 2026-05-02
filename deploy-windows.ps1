# =============================================================================
#  deploy-windows.ps1  —  Install DB Sync as a Windows Service
#  Requires: NSSM (download nssm.exe and place it in this folder)
#  Download NSSM: https://nssm.cc/download
# =============================================================================
#
#  WHAT IT DOES:
#    Registers sync.py as a Windows Service that starts automatically on boot
#    and restarts itself if it ever crashes.
#
#  HOW TO RUN (open PowerShell as Administrator):
#    .\deploy-windows.ps1 -Push           # send local data → remote
#    .\deploy-windows.ps1 -Pull           # receive remote data → local
#
#  OPTIONS:
#    -Push             local → remote  (use on the machine that writes data)
#    -Pull             remote → local  (use on the machine that reads data)
#    -Watch N          how often to sync in seconds  (default: value in .env)
#    -Name  NAME       service name  (default: db-sync)
#    -EnvFile FILE     path to a custom .env file  (default: .env in this folder)
#
#  EXAMPLES:
#    .\deploy-windows.ps1 -Push -Watch 30
#    .\deploy-windows.ps1 -Pull -Watch 60
#    .\deploy-windows.ps1 -Push -Watch 30 -Name myapp-sync
#
# =============================================================================

param(
    [switch]$Push,
    [switch]$Pull,
    [int]   $Watch   = 0,
    [string]$Name    = "db-sync",
    [string]$EnvFile = ""
)

# ── Where is this project? ────────────────────────────────────────────────────
$ProjectPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Nssm        = "$ProjectPath\nssm.exe"
$Python      = "$ProjectPath\venv\Scripts\python.exe"
$Script      = "$ProjectPath\sync.py"

# ── Require -Push or -Pull ────────────────────────────────────────────────────
if (-not $Push -and -not $Pull) {
    Write-Host ""
    Write-Host "  ERROR: You must choose a direction." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Send local data to remote server:"
    Write-Host "    .\deploy-windows.ps1 -Push"
    Write-Host ""
    Write-Host "  Receive remote data to this machine:"
    Write-Host "    .\deploy-windows.ps1 -Pull"
    Write-Host ""
    exit 1
}

if ($Push -and $Pull) {
    Write-Host ""
    Write-Host "  ERROR: Use -Push or -Pull, not both at the same time." -ForegroundColor Red
    Write-Host ""
    exit 1
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ""
    Write-Host "  ERROR: Right-click deploy-windows.ps1 and choose 'Run as Administrator'" -ForegroundColor Red
    Write-Host ""
    exit 1
}

if (-not (Test-Path $Nssm)) {
    Write-Host ""
    Write-Host "  ERROR: nssm.exe not found in this folder." -ForegroundColor Red
    Write-Host "  Download it from https://nssm.cc/download" -ForegroundColor Yellow
    Write-Host "  Then place nssm.exe in: $ProjectPath" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

if (-not (Test-Path $Python)) {
    Write-Host ""
    Write-Host "  ERROR: Python virtual environment not found." -ForegroundColor Red
    Write-Host "  Run these commands first:" -ForegroundColor Yellow
    Write-Host "    python -m venv venv" -ForegroundColor Yellow
    Write-Host "    venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$envCheck = if ($EnvFile) { $EnvFile } else { "$ProjectPath\.env" }
if (-not (Test-Path $envCheck)) {
    Write-Host ""
    Write-Host "  ERROR: .env file not found at: $envCheck" -ForegroundColor Red
    Write-Host "  Copy .env.example to .env and fill in your database details." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# ── Build the sync command ────────────────────────────────────────────────────
$Direction = if ($Push) { "--push" } else { "--pull" }
$SyncArgs  = "$Direction --watch"
if ($Watch -gt 0) { $SyncArgs += " $Watch" }
if ($EnvFile)     { $SyncArgs += " --env `"$EnvFile`"" }

# ── Remove old service if it exists ──────────────────────────────────────────
if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
    Write-Host "  Removing old service '$Name'..." -ForegroundColor Yellow
    & $Nssm stop   $Name 2>$null
    & $Nssm remove $Name confirm 2>$null
    Start-Sleep -Seconds 2
}

# ── Install the service ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Installing service '$Name'..." -ForegroundColor Cyan
Write-Host "  Direction : $Direction"
Write-Host "  Interval  : $(if ($Watch -gt 0) { "${Watch}s" } else { 'from .env' })"
Write-Host ""

& $Nssm install $Name $Python "$Script $SyncArgs"
& $Nssm set     $Name AppDirectory   $ProjectPath
& $Nssm set     $Name AppRestartDelay 5000
& $Nssm set     $Name AppStdout      "$ProjectPath\sync_out.log"
& $Nssm set     $Name AppStderr      "$ProjectPath\sync_err.log"
& $Nssm set     $Name AppRotateFiles 1
& $Nssm set     $Name AppRotateBytes 1048576
& $Nssm set     $Name Start          SERVICE_AUTO_START
& $Nssm start   $Name

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ✅  Service '$Name' is installed and running." -ForegroundColor Green
Write-Host ""
Write-Host "  ─── Useful commands ───────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  Check status  :  .\nssm.exe status  $Name"
Write-Host "  Stop          :  .\nssm.exe stop    $Name"
Write-Host "  Start         :  .\nssm.exe start   $Name"
Write-Host "  Restart       :  .\nssm.exe restart $Name"
Write-Host "  View logs     :  Get-Content $ProjectPath\sync_out.log -Wait"
Write-Host ""
Write-Host "  ─── Remove service completely ─────────────────────────────" -ForegroundColor Cyan
Write-Host "  .\nssm.exe stop   $Name"
Write-Host "  .\nssm.exe remove $Name confirm"
Write-Host ""
