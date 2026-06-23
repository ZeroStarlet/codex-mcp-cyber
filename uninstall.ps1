# codex-mcp-cyber Uninstall Script for Windows
# Removes MCP server registration and local venv

param(
    [switch]$WhatIf,
    [switch]$Help
)

if ($Help) {
    Write-Host @"
codex-mcp-cyber Uninstall Script for Windows

Usage: .\uninstall.ps1 [-WhatIf] [-Help]

Options:
  -WhatIf    Dry-run mode
  -Help      Show this help

Examples:
  .\uninstall.ps1           # Run the uninstall
  .\uninstall.ps1 -WhatIf   # Preview what would be done
"@
    exit 0
}

$DryRun = $WhatIf.IsPresent

$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProgressPreference = "SilentlyContinue"

function Write-Step {
    param([string]$Message)
    Write-Host "`n[*] $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-WarningMsg {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-DryRun {
    param([string]$Message)
    Write-Host "[DRY-RUN] $Message" -ForegroundColor Magenta
}

function Invoke-Claude {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [int]$TimeoutSec = 30
    )
    $tmpIn = $tmpOut = $tmpErr = $null
    try {
        $tmpIn  = [System.IO.Path]::GetTempFileName()
        $tmpOut = [System.IO.Path]::GetTempFileName()
        $tmpErr = [System.IO.Path]::GetTempFileName()
        $quotedArgs = $Arguments | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }
        $cmdArgs = @("/c", "claude") + $quotedArgs
        $p = Start-Process cmd -ArgumentList $cmdArgs -NoNewWindow -PassThru `
             -RedirectStandardInput $tmpIn `
             -RedirectStandardOutput $tmpOut `
             -RedirectStandardError $tmpErr
        $p | Wait-Process -Timeout $TimeoutSec -ErrorAction SilentlyContinue
        if (!$p.HasExited) {
            try { $p | Stop-Process -Force } catch {}
            return @{ ExitCode = -1; Output = "Timed out after ${TimeoutSec}s"; TimedOut = $true }
        }
        $out = if (Test-Path $tmpOut) { Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue } else { "" }
        $err = if (Test-Path $tmpErr) { Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue } else { "" }
        $combined = (($out, $err) | Where-Object { $_ }) -join "`n"
        return @{ ExitCode = $p.ExitCode; Output = $combined; TimedOut = $false }
    } catch {
        return @{ ExitCode = -1; Output = $_.Exception.Message; TimedOut = $false }
    } finally {
        @($tmpIn, $tmpOut, $tmpErr) | Where-Object { $_ } | ForEach-Object {
            Remove-Item $_ -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($DryRun) {
    Write-Host "`n============================================================" -ForegroundColor Magenta
    Write-Host "  DRY-RUN MODE - No changes will be made" -ForegroundColor Magenta
    Write-Host "============================================================`n" -ForegroundColor Magenta
}

# ======================================================================
# Step 1: Check claude CLI
# ======================================================================
Write-Step "Step 1: Checking claude CLI..."

$claudeAvailable = $false
try {
    $null = claude --version 2>&1
    $claudeAvailable = $true
    Write-Success "claude CLI is available"
} catch {
    Write-WarningMsg "claude CLI not available, skipping MCP removal"
}

# ======================================================================
# Step 2: Remove MCP server
# ======================================================================
Write-Step "Step 2: Removing MCP server registration..."

if (-not $claudeAvailable) {
    Write-WarningMsg "Skipping: claude CLI not available"
} elseif ($DryRun) {
    Write-DryRun "Would run: claude mcp remove codex-mcp-cyber --scope user"
} else {
    $result = Invoke-Claude -Arguments @("mcp","remove","codex-mcp-cyber","--scope","user") -TimeoutSec 15
    if ($result.TimedOut) {
        Write-ErrorMsg "claude mcp remove timed out"
    } elseif ($result.ExitCode -eq 0) {
        Write-Success "MCP server 'codex-mcp-cyber' removed"
    } else {
        Write-WarningMsg "MCP server 'codex-mcp-cyber' was not registered (nothing to remove)"
    }
}

# ======================================================================
# Step 3: Remove local venv
# ======================================================================
Write-Step "Step 3: Removing local virtual environment..."

$venvDir = Join-Path $PSScriptRoot ".venv"

if (-not (Test-Path $venvDir)) {
    Write-WarningMsg "Virtual environment not found, skipping"
} elseif ($DryRun) {
    Write-DryRun "Would remove: $venvDir"
} else {
    $confirm = Read-Host "Remove local virtual environment $venvDir? (y/N)"
    if ($confirm -eq "y" -or $confirm -eq "Y") {
        $venvProcs = Get-Process | Where-Object {
            $_.Path -and $_.Path.StartsWith($venvDir, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($venvProcs) {
            Write-WarningMsg "Stopping processes: $($venvProcs.Name -join ', ')"
            $venvProcs | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }

        Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue

        if (Test-Path $venvDir) {
            cmd /c rmdir /s /q "`"$venvDir`"" 2>$null
        }
        if (Test-Path $venvDir) {
            Write-ErrorMsg "Could not fully remove $venvDir (files may be locked)"
        } else {
            Write-Success "Removed virtual environment"
        }
    } else {
        Write-WarningMsg "Skipping virtual environment removal"
    }
}

# ======================================================================
# Done
# ======================================================================
if ($DryRun) {
    Write-Host "`n============================================================" -ForegroundColor Magenta
    Write-Host "  DRY-RUN COMPLETED - No changes were made" -ForegroundColor Magenta
    Write-Host "============================================================`n" -ForegroundColor Magenta
    Write-Host "Run without -WhatIf:" -ForegroundColor Cyan
    Write-Host "  .\uninstall.ps1" -ForegroundColor White
} else {
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Success "codex-mcp-cyber uninstall completed!"
    Write-Host "============================================================`n" -ForegroundColor Green
}
Write-Host ""
