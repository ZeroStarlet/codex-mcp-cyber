# SCCG Uninstall Script for Windows
# This script removes all components installed by setup.ps1

param(
    [switch]$WhatIf,
    [switch]$Help
)

# Show help
if ($Help) {
    Write-Host @"
SCCG Uninstall Script for Windows

Usage: .\uninstall.ps1 [-WhatIf] [-Help]

Options:
  -WhatIf    Dry-run mode. Show what would be done without making changes.
  -Help      Show this help message.

Examples:
  .\uninstall.ps1           # Run the uninstall
  .\uninstall.ps1 -WhatIf   # Preview what would be done
"@
    exit 0
}

$DryRun = $WhatIf.IsPresent

# Force UTF-8 encoding for file operations
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Do NOT use $ErrorActionPreference = "Stop" — steps are independent
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

# Helper: run claude CLI with closed stdin and timeout (prevents hanging)
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

# ==============================================================================
# Dry-run mode banner
# ==============================================================================
if ($DryRun) {
    Write-Host "`n============================================================" -ForegroundColor Magenta
    Write-Host "  DRY-RUN MODE - No changes will be made" -ForegroundColor Magenta
    Write-Host "============================================================`n" -ForegroundColor Magenta
}

# ==============================================================================
# Step 1: Check claude CLI
# ==============================================================================
Write-Step "Step 1: Checking claude CLI..."

$claudeAvailable = $false
try {
    $null = claude --version 2>&1
    $claudeAvailable = $true
    Write-Success "claude CLI is available"
} catch {
    Write-WarningMsg "claude CLI is not available, will skip MCP server removal"
}

# ==============================================================================
# Step 2: Remove MCP server
# ==============================================================================
Write-Step "Step 2: Removing MCP server registration..."

if (-not $claudeAvailable) {
    Write-WarningMsg "Skipping: claude CLI not available"
} elseif ($DryRun) {
    Write-DryRun "Would run: claude mcp remove sccg --scope user"
} else {
    $result = Invoke-Claude -Arguments @("mcp","remove","sccg","--scope","user") -TimeoutSec 15
    if ($result.TimedOut) {
        Write-ErrorMsg "claude mcp remove timed out"
    } elseif ($result.ExitCode -eq 0) {
        Write-Success "MCP server 'sccg' removed"
    } else {
        Write-WarningMsg "MCP server 'sccg' was not registered (nothing to remove)"
    }
}

# ==============================================================================
# Step 3: Remove Skills
# ==============================================================================
Write-Step "Step 3: Removing Skills..."

$skillsDir = "$env:USERPROFILE\.claude\skills"
$skillNames = @("sccg-workflow", "gemini-collaboration")

foreach ($skill in $skillNames) {
    $skillPath = Join-Path $skillsDir $skill
    if (Test-Path $skillPath) {
        if ($DryRun) {
            Write-DryRun "Would remove: $skillPath"
        } else {
            try {
                Remove-Item -Recurse -Force $skillPath
                Write-Success "Removed skill: $skill"
            } catch {
                Write-ErrorMsg "Failed to remove skill $skill`: $_"
            }
        }
    } else {
        Write-WarningMsg "Skill not found, skipping: $skill"
    }
}

# ==============================================================================
# Step 4: Clean CLAUDE.md
# ==============================================================================
Write-Step "Step 4: Cleaning global CLAUDE.md..."

$claudeMdPath = "$env:USERPROFILE\.claude\CLAUDE.md"
$sccgMarker = "# SCCG Configuration"

if (-not (Test-Path $claudeMdPath)) {
    Write-WarningMsg "CLAUDE.md not found, skipping"
} else {
    $content = Get-Content $claudeMdPath -Raw -Encoding UTF8
    if (-not ($content -match [regex]::Escape($sccgMarker))) {
        Write-WarningMsg "No SCCG configuration found in CLAUDE.md, skipping"
    } else {
        # Split at the marker
        $markerIndex = $content.IndexOf($sccgMarker)
        $beforeMarker = $content.Substring(0, $markerIndex)

        # Trim trailing whitespace/newlines from the content before marker
        $beforeMarker = $beforeMarker.TrimEnd()

        if ([string]::IsNullOrWhiteSpace($beforeMarker)) {
            # File was entirely SCCG config (or only whitespace before it) -> delete file
            if ($DryRun) {
                Write-DryRun "Would delete: $claudeMdPath (file contains only SCCG configuration)"
            } else {
                try {
                    Remove-Item $claudeMdPath -Force
                    Write-Success "Deleted CLAUDE.md (contained only SCCG configuration)"
                } catch {
                    Write-ErrorMsg "Failed to delete CLAUDE.md: $_"
                }
            }
        } else {
            # Content exists before marker -> truncate at marker
            # Add a trailing newline for clean file ending
            $newContent = $beforeMarker + "`n"
            if ($DryRun) {
                Write-DryRun "Would remove SCCG configuration block from: $claudeMdPath"
            } else {
                try {
                    [System.IO.File]::WriteAllText($claudeMdPath, $newContent, [System.Text.UTF8Encoding]::new($false))
                    Write-Success "Removed SCCG configuration from CLAUDE.md"
                } catch {
                    Write-ErrorMsg "Failed to clean CLAUDE.md: $_"
                }
            }
        }
    }
}

# ==============================================================================
# Step 5: Remove Coder config
# ==============================================================================
Write-Step "Step 5: Removing Coder configuration..."

$configDir = "$env:USERPROFILE\.sccg-mcp"

if (-not (Test-Path $configDir)) {
    Write-WarningMsg "Config directory not found ($configDir), skipping"
} elseif ($DryRun) {
    Write-DryRun "Would remove: $configDir (contains config.toml with API token)"
} else {
    $confirm = Read-Host "Remove Coder config directory $configDir (contains API token)? (y/N)"
    if ($confirm -eq "y" -or $confirm -eq "Y") {
        try {
            Remove-Item -Recurse -Force $configDir
            Write-Success "Removed Coder config directory: $configDir"
        } catch {
            Write-ErrorMsg "Failed to remove config directory: $_"
        }
    } else {
        Write-WarningMsg "Skipping Coder config removal"
    }
}

# ==============================================================================
# Step 6: Remove local venv
# ==============================================================================
Write-Step "Step 6: Removing local virtual environment..."

$venvDir = Join-Path $PSScriptRoot ".venv"

if (-not (Test-Path $venvDir)) {
    Write-WarningMsg "Virtual environment not found ($venvDir), skipping"
} elseif ($DryRun) {
    Write-DryRun "Would remove: $venvDir"
} else {
    $confirm = Read-Host "Remove local virtual environment $venvDir? (y/N)"
    if ($confirm -eq "y" -or $confirm -eq "Y") {
        # Kill processes running from .venv (e.g. sccg-mcp.exe, python.exe)
        $venvProcs = Get-Process | Where-Object {
            $_.Path -and $_.Path.StartsWith($venvDir, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($venvProcs) {
            Write-WarningMsg "Stopping processes using venv: $($venvProcs.Name -join ', ')"
            $venvProcs | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }

        # Attempt removal (SilentlyContinue: non-terminating errors won't print)
        Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue

        # Verify and fallback
        if (Test-Path $venvDir) {
            cmd /c rmdir /s /q "`"$venvDir`"" 2>$null
        }
        if (Test-Path $venvDir) {
            Write-ErrorMsg "Could not fully remove $venvDir (files locked by running processes)"
            Write-Host "  Close Claude Code and run this script again, or delete manually." -ForegroundColor Yellow
        } else {
            Write-Success "Removed virtual environment: $venvDir"
        }
    } else {
        Write-WarningMsg "Skipping virtual environment removal"
    }
}

# ==============================================================================
# Done!
# ==============================================================================
if ($DryRun) {
    Write-Host "`n============================================================" -ForegroundColor Magenta
    Write-Host "  DRY-RUN COMPLETED - No changes were made" -ForegroundColor Magenta
    Write-Host "============================================================`n" -ForegroundColor Magenta
    Write-Host "Run without -WhatIf to apply changes:" -ForegroundColor Cyan
    Write-Host "  .\uninstall.ps1" -ForegroundColor White
} else {
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Success "SCCG uninstall completed!"
    Write-Host "============================================================`n" -ForegroundColor Green

    Write-Host "The following shared tools were NOT removed:" -ForegroundColor Cyan
    Write-Host "  - uv (package manager)" -ForegroundColor White
    Write-Host "  - claude CLI" -ForegroundColor White
    Write-Host ""
    Write-Host "To reinstall SCCG, run:" -ForegroundColor Cyan
    Write-Host "  .\setup.ps1" -ForegroundColor White
}
Write-Host ""
