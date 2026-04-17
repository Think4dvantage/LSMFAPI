#!/usr/bin/env pwsh
<#
.SYNOPSIS
    LSMFAPI remote deployment and management script.

.DESCRIPTION
    Manages the LSMFAPI dev stack running on a remote Docker host via SSH.
    Requires only the built-in Windows SSH client (OpenSSH, shipped with
    Windows 10 1803+ and Windows 11).

    SSH alias   : xpsex  (defined in ~/.ssh/config — hostname, user and
                          identity file are all resolved from there)
    Remote path : ~/lsmfapi

.EXAMPLE
    # First-time setup — verify SSH, create remote directory, remind about config.yml
    .\scripts\LSMF-dev.ps1 setup

    # Push code and (re)build + start services
    .\scripts\LSMF-dev.ps1 deploy

    # Just sync code without restarting services
    .\scripts\LSMF-dev.ps1 sync

    # Start / stop / restart services
    .\scripts\LSMF-dev.ps1 up
    .\scripts\LSMF-dev.ps1 down
    .\scripts\LSMF-dev.ps1 restart

    # Tail live logs (Ctrl+C to exit)
    .\scripts\LSMF-dev.ps1 logs
    .\scripts\LSMF-dev.ps1 logs lsmfapi   # single service

    # View running containers
    .\scripts\LSMF-dev.ps1 status

    # Open a shell on the remote host
    .\scripts\LSMF-dev.ps1 shell

    # Open a shell inside the app container
    .\scripts\LSMF-dev.ps1 exec
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "sync", "deploy", "up", "down", "restart", "logs", "status", "shell", "exec", "help")]
    [string]$Command = "help",

    [Parameter(Position = 1)]
    [string]$Arg = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
$SSH_TARGET  = "xpsex"
$REMOTE_DIR  = "/opt/LSMF"

$COMPOSE_CMD = "docker compose --project-name lsmfapi-dev -f docker-compose.yml -f docker-compose.dev.yml"

$SYNC_EXCLUDES = @(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    # config.yml is tracked in git and synced automatically
    "lsmfapi.db",
    "data",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".vscode",
    ".ai"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Header([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Invoke-SSH([string]$remoteCmd, [switch]$Interactive) {
    if ($Interactive) {
        ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -t "$SSH_TARGET" $remoteCmd
    } else {
        ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "$SSH_TARGET" $remoteCmd
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SSH command failed (exit $LASTEXITCODE): $remoteCmd"
    }
}

function Sync-Files {
    Write-Header "Syncing project files → ${SSH_TARGET}:${REMOTE_DIR}"

    $localDir = (Get-Location).Path
    Write-Host "  Source : $localDir" -ForegroundColor Gray
    Write-Host "  Target : ${SSH_TARGET}:${REMOTE_DIR}" -ForegroundColor Gray

    $excludeArgs = ($SYNC_EXCLUDES | ForEach-Object { "--exclude=./$_" })

    $tmpTar = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.tar.gz'
    try {
        $tarArgs = @("-czf", $tmpTar) + $excludeArgs + @("-C", $localDir, ".")
        & tar @tarArgs
        if ($LASTEXITCODE -ne 0) { Write-Error "tar failed creating archive" }

        $tmpSize = [math]::Round((Get-Item $tmpTar).Length / 1MB, 1)
        Write-Host "  Archive: $tmpSize MB" -ForegroundColor Gray

        Write-Host "  Uploading and extracting…" -ForegroundColor Gray
        $proc = Start-Process -FilePath "ssh" `
            -ArgumentList @(
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=4",
                "$SSH_TARGET",
                "sudo mkdir -p $REMOTE_DIR && sudo chown `$USER:`$USER $REMOTE_DIR && /usr/bin/tar xzf - -C $REMOTE_DIR"
            ) `
            -RedirectStandardInput $tmpTar `
            -NoNewWindow -Wait -PassThru

        if ($proc.ExitCode -ne 0) {
            Write-Error "SSH stream-extract failed (exit $($proc.ExitCode))"
        }
    } finally {
        Remove-Item $tmpTar -ErrorAction SilentlyContinue
    }

    Write-Host "  Done." -ForegroundColor Green
}

function Test-SSHConnectivity {
    Write-Header "Testing SSH connectivity to $SSH_TARGET"
    $result = ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o BatchMode=yes "$SSH_TARGET" "echo ok" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Cannot connect. Check:" -ForegroundColor Yellow
        Write-Host "    1. SSH alias exists : entry 'Host xpsex' in ~/.ssh/config" -ForegroundColor Yellow
        Write-Host "    2. Key is authorised: ssh-copy-id -i <pub key> <user@host>" -ForegroundColor Yellow
        Write-Host "    3. Host is reachable from this network" -ForegroundColor Yellow
        return $false
    }
    Write-Host "  SSH OK" -ForegroundColor Green
    return $true
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

function Invoke-Setup {
    Write-Header "First-time setup"

    Write-Host "  SSH alias : $SSH_TARGET" -ForegroundColor Gray
    Write-Host "  Ensure 'Host $SSH_TARGET' is defined in ~/.ssh/config with" -ForegroundColor Gray
    Write-Host "  HostName, User, and IdentityFile set correctly." -ForegroundColor Gray
    Write-Host ""

    if (-not (Test-SSHConnectivity)) { exit 1 }

    Write-Header "Preparing remote host"
    Invoke-SSH "sudo mkdir -p $REMOTE_DIR && sudo chown `$USER:`$USER $REMOTE_DIR"
    Invoke-SSH "docker --version && docker compose version"

    Write-Host ""
    Write-Host "Setup complete. Run:" -ForegroundColor Green
    Write-Host "  .\scripts\LSMF-dev.ps1 deploy" -ForegroundColor White
}

function Test-RemoteConfig {
    # Returns true if config.yml exists as a regular file on the remote host.
    $result = ssh -o ServerAliveInterval=15 "$SSH_TARGET" "test -f $REMOTE_DIR/config.yml && echo ok" 2>&1
    if ($result -ne "ok") {
        Write-Host ""
        Write-Host "  config.yml not found on remote host." -ForegroundColor Red
        Write-Host "  Docker will create it as a directory and the app will crash." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Fix:" -ForegroundColor White
        Write-Host "    ssh $SSH_TARGET `"rm -rf $REMOTE_DIR/config.yml`"" -ForegroundColor White
        Write-Host "    scp config.yml ${SSH_TARGET}:${REMOTE_DIR}/config.yml" -ForegroundColor White
        Write-Host ""
        return $false
    }
    return $true
}

function Invoke-Deploy {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    if (-not (Test-RemoteConfig)) { exit 1 }
    Sync-Files

    Write-Header "Building and starting DEV services on $SSH_TARGET"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up --build -d"
    Write-Host ""
    Write-Host "Services started." -ForegroundColor Green
    Write-Host "  API  : https://lsmfapi-dev.lg4.ch" -ForegroundColor Green
    Write-Host "  Docs : https://lsmfapi-dev.lg4.ch/docs" -ForegroundColor Green
    Write-Host ""
    Write-Host "Tail logs with: .\scripts\LSMF-dev.ps1 logs" -ForegroundColor Gray
}

function Invoke-Sync {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Sync-Files
    Write-Host ""
    Write-Host "Files synced. To apply: .\scripts\LSMF-dev.ps1 restart" -ForegroundColor Gray
}

function Invoke-Up {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Starting services"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up -d"
    Write-Host "  https://lsmfapi-dev.lg4.ch" -ForegroundColor Green
}

function Invoke-Down {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Stopping services"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD down"
}

function Invoke-Restart {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Restarting services"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD restart"
    Write-Host "  https://lsmfapi-dev.lg4.ch" -ForegroundColor Green
}

function Invoke-Logs([string]$service = "") {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Streaming logs (Ctrl+C to stop)"
    $svcArg = if ($service) { " $service" } else { "" }
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD logs -f --tail=100$svcArg" -Interactive
}

function Invoke-Status {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Container status on $SSH_TARGET"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD ps"
}

function Invoke-Shell {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Opening shell on $SSH_TARGET"
    ssh -t "$SSH_TARGET" "bash -l"
}

function Invoke-Exec {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Opening shell inside lsmfapi container"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD exec lsmfapi bash" -Interactive
}

function Show-Help {
    Write-Host @"

LSMFAPI Remote Management
  SSH alias : $SSH_TARGET  (resolved via ~/.ssh/config)
  Remote dir: $REMOTE_DIR

Commands:
  setup     Verify SSH connectivity and prepare remote directory
  sync      Push code to remote (no service restart)
  deploy    sync + docker compose up --build  (full redeploy)
  up        Start services (no rebuild)
  down      Stop and remove containers
  restart   Restart running containers
  logs      Tail compose logs  (optional: logs lsmfapi)
  status    Show container status
  shell     SSH into the remote host
  exec      Open bash inside the lsmfapi container

Note: hostname, user and identity file are all defined in ~/.ssh/config
      under 'Host $SSH_TARGET'. The script passes no -i flag or user@host.
"@ -ForegroundColor White
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
Push-Location $PSScriptRoot\..

switch ($Command) {
    "setup"   { Invoke-Setup }
    "sync"    { Invoke-Sync }
    "deploy"  { Invoke-Deploy }
    "up"      { Invoke-Up }
    "down"    { Invoke-Down }
    "restart" { Invoke-Restart }
    "logs"    { Invoke-Logs $Arg }
    "status"  { Invoke-Status }
    "shell"   { Invoke-Shell }
    "exec"    { Invoke-Exec }
    default   { Show-Help }
}

Pop-Location
