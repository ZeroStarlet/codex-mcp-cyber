# codex-mcp-cyber Setup Script for Windows
# Automates MCP server installation and registration

param(
    [switch]$WhatIf,
    [switch]$Help
)

if ($Help) {
    Write-Host @"
codex-mcp-cyber Setup Script for Windows

Usage: .\setup.ps1 [-WhatIf] [-Help]

Options:
  -WhatIf    Dry-run mode
  -Help      Show this help

Examples:
  .\setup.ps1           # Run the setup
  .\setup.ps1 -WhatIf   # Preview what would be done
"@
    exit 0
}

$DryRun = $WhatIf.IsPresent

$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:UV_LINK_MODE = "copy"

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

function Refresh-PathFromRegistry {
    $registryPath = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $currentPath = $env:Path
    $currentPaths = $currentPath -split ';' | Where-Object { $_ -ne '' }
    $registryPaths = $registryPath -split ';' | Where-Object { $_ -ne '' }
    $newPaths = $registryPaths | Where-Object { $_ -notin $currentPaths }
    if ($newPaths) {
        $env:Path = $currentPath + ";" + ($newPaths -join ';')
    }
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
# Step 1: Check dependencies
# ======================================================================
Write-Step "Step 1: Checking dependencies..."

$uvInstalled = $false
try {
    $null = uv --version 2>&1
    $uvInstalled = $true
    Write-Success "uv is installed"
} catch {
    Refresh-PathFromRegistry
    try {
        $null = uv --version 2>&1
        $uvInstalled = $true
        Write-Success "uv is installed"
    } catch {
        if ($DryRun) {
            Write-WarningMsg "uv is not installed"
            Write-DryRun "Would install uv automatically"
        } else {
            Write-WarningMsg "uv is not installed, installing..."
            try {
                powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
                Refresh-PathFromRegistry
                $null = uv --version 2>&1
                $uvInstalled = $true
                Write-Success "uv installed"
            } catch {
                Write-ErrorMsg "Failed to install uv"
                Write-Host "Install manually: https://github.com/astral-sh/uv" -ForegroundColor Yellow
                exit 1
            }
        }
    }
}

$claudeInstalled = $false
try {
    $null = claude --version 2>&1
    $claudeInstalled = $true
    Write-Success "claude CLI is installed"
} catch {
    Refresh-PathFromRegistry
    try {
        $null = claude --version 2>&1
        $claudeInstalled = $true
        Write-Success "claude CLI is installed"
    } catch {
        if ($DryRun) {
            Write-WarningMsg "claude CLI is not installed"
            Write-DryRun "Would require claude CLI before running"
        } else {
            Write-ErrorMsg "claude CLI is not installed"
            Write-Host "Install: npm install -g @anthropic-ai/claude-code" -ForegroundColor Yellow
            exit 1
        }
    }
}

# ======================================================================
# Step 2: Install project dependencies
# ======================================================================
Write-Step "Step 2: Installing project dependencies..."

if ($DryRun) {
    Write-DryRun "Would run: uv sync"
    Write-Success "Project dependencies would be installed"
} else {
    uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-ErrorMsg "Failed to install dependencies"
        exit 1
    }

    $venvPython = [System.IO.Path]::Combine($PSScriptRoot, ".venv", "Scripts", "python.exe")
    if (Test-Path $venvPython) {
        & $venvPython -c "from mcp.server.fastmcp import FastMCP" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Fixing pywin32 for Windows..." -ForegroundColor Yellow
            uv pip install pywin32 --reinstall --quiet 2>$null
        }
    }

    Write-Success "Project dependencies installed"
}

# ======================================================================
# Step 3: Register MCP server
# ======================================================================
Write-Step "Step 3: Registering MCP server..."

if ($DryRun) {
    Write-DryRun "Would run: claude mcp remove sccg --scope user"
    Write-DryRun "Would run: claude mcp remove codex-mcp-cyber --scope user"

    Write-Host ""
    Write-Host "Select installation method:"
    Write-Host "  1) Remote install (recommended) - Auto-fetches latest version from GitHub"
    Write-Host "  2) Local install - Uses current project directory (for development)"
    $installMethod = Read-Host "Enter choice [1]"
    if ([string]::IsNullOrWhiteSpace($installMethod)) { $installMethod = "1" }

    if ($installMethod -eq "2") {
        Write-DryRun "Would run: claude mcp add codex-mcp-cyber --scope user --transport stdio -- uv run --directory `"$PSScriptRoot`" codex-mcp-cyber"
    } else {
        Write-DryRun "Would run: claude mcp add codex-mcp-cyber --scope user --transport stdio -- uvx --refresh --from git+https://github.com/ZeroStarlet/codex-mcp-cyber.git codex-mcp-cyber"
    }
    Write-Success "MCP server would be registered"
} else {
    $oldErrorActionPreference = $ErrorActionPreference
    $oldNativeCommandEap = $null
    $ErrorActionPreference = "Continue"
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $oldNativeCommandEap = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        # Clean up old SCCG registration for upgrading users
        $sccgRemoveResult = Invoke-Claude -Arguments @("mcp","remove","sccg","--scope","user") -TimeoutSec 15
        if ($sccgRemoveResult.ExitCode -eq 0) {
            Write-WarningMsg "Removed legacy sccg MCP server (upgrade cleanup)"
        }

        $removeResult = Invoke-Claude -Arguments @("mcp","remove","codex-mcp-cyber","--scope","user") -TimeoutSec 15
        if ($removeResult.TimedOut) {
            Write-WarningMsg "claude mcp remove timed out, skipping"
        } elseif ($removeResult.ExitCode -eq 0) {
            Write-WarningMsg "Removed existing codex-mcp-cyber MCP server"
        }

        Write-Host ""
        Write-Host "Select installation method:"
        Write-Host "  1) Remote install (recommended) - Auto-fetches latest version from GitHub"
        Write-Host "  2) Local install - Uses current project directory (for development)"
        $installMethod = Read-Host "Enter choice [1]"
        if ([string]::IsNullOrWhiteSpace($installMethod)) { $installMethod = "1" }

        if ($installMethod -eq "2") {
            $localResult = Invoke-Claude -Arguments @("mcp","add","codex-mcp-cyber","--scope","user","--transport","stdio","--","uv","run","--directory",$PSScriptRoot,"codex-mcp-cyber") -TimeoutSec 60
            $localSucceeded = $localResult.ExitCode -eq 0
            $localOutput = $localResult.Output
            if ($localSucceeded) {
                Write-Success "MCP server registered (local install from $PSScriptRoot)"
            } else {
                Write-ErrorMsg "Failed to register MCP server (local install)"
                Write-Host "Error: $localOutput" -ForegroundColor Red
                exit 1
            }
        } else {
            $mcpRegistered = $false
            $lastError = ""

            $remoteResult = Invoke-Claude -Arguments @("mcp","add","codex-mcp-cyber","--scope","user","--transport","stdio","--","uvx","--refresh","--from","git+https://github.com/ZeroStarlet/codex-mcp-cyber.git","codex-mcp-cyber") -TimeoutSec 60
            $remoteSucceeded = $remoteResult.ExitCode -eq 0
            $remoteOutput = $remoteResult.Output

            if ($remoteSucceeded) {
                $mcpRegistered = $true
                Write-Success "MCP server registered"
            } else {
                $lastError = $remoteOutput
            }

            if (-not $mcpRegistered) {
                Write-ErrorMsg "Failed to register MCP server"
                Write-Host "Error: $lastError" -ForegroundColor Red
                exit 1
            }
        }
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            $PSNativeCommandUseErrorActionPreference = $oldNativeCommandEap
        }
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
    Write-Host "  .\setup.ps1" -ForegroundColor White
} else {
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Success "codex-mcp-cyber setup completed!"
    Write-Host "============================================================`n" -ForegroundColor Green

    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Restart Claude Code CLI" -ForegroundColor White
    Write-Host "  2. Verify: claude mcp list" -ForegroundColor White
}
Write-Host ""
