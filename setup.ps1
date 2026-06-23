# SCCG One-Click Setup Script for Windows
# This script automates the setup of Sisyphus-Coder-Codex-Gemini MCP server

param(
    [switch]$WhatIf,
    [switch]$Help
)

# Show help
if ($Help) {
    Write-Host @"
SCCG One-Click Setup Script for Windows

Usage: .\setup.ps1 [-WhatIf] [-Help]

Options:
  -WhatIf    Dry-run mode. Show what would be done without making changes.
  -Help      Show this help message.

Examples:
  .\setup.ps1           # Run the setup
  .\setup.ps1 -WhatIf   # Preview what would be done
"@
    exit 0
}

$DryRun = $WhatIf.IsPresent

# Force UTF-8 encoding for file operations
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Suppress uv hardlink warnings (cross-filesystem fallback)
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
        # Use cmd /c to invoke claude �?npm-installed .cmd scripts cannot be
        # executed directly by Start-Process when UseShellExecute is false
        # (which is implied by -RedirectStandard*).
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
# Step 1: Check dependencies
# ==============================================================================
Write-Step "Step 1: Checking dependencies..."

# Helper function to refresh PATH by merging registry PATH with current session PATH
function Refresh-PathFromRegistry {
    $registryPath = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $currentPath = $env:Path
    # Merge: add registry paths that are not already in current PATH
    $currentPaths = $currentPath -split ';' | Where-Object { $_ -ne '' }
    $registryPaths = $registryPath -split ';' | Where-Object { $_ -ne '' }
    $newPaths = $registryPaths | Where-Object { $_ -notin $currentPaths }
    if ($newPaths) {
        $env:Path = $currentPath + ";" + ($newPaths -join ';')
    }
}

# Check uv
$uvInstalled = $false
try {
    $null = uv --version 2>&1
    $uvInstalled = $true
    Write-Success "uv is installed"
} catch {
    # Try refreshing PATH from registry (may help find tools installed by npm, scoop, etc.)
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
            Write-WarningMsg "uv is not installed, installing automatically..."
            try {
                powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
                # Refresh PATH again after installation
                Refresh-PathFromRegistry
                $null = uv --version 2>&1
                $uvInstalled = $true
                Write-Success "uv installed successfully"
            } catch {
                Write-ErrorMsg "Failed to install uv automatically"
                Write-Host "Please install uv manually: https://github.com/astral-sh/uv" -ForegroundColor Yellow
                exit 1
            }
        }
    }
}

# Check claude CLI
$claudeInstalled = $false
try {
    $null = claude --version 2>&1
    $claudeInstalled = $true
    Write-Success "claude CLI is installed"
} catch {
    # Try refreshing PATH from registry (may help find tools installed by npm, scoop, etc.)
    Refresh-PathFromRegistry
    try {
        $null = claude --version 2>&1
        $claudeInstalled = $true
        Write-Success "claude CLI is installed"
    } catch {
        if ($DryRun) {
            Write-WarningMsg "claude CLI is not installed"
            Write-DryRun "Would require claude CLI to be installed before running"
        } else {
            Write-ErrorMsg "claude CLI is not installed"
            Write-Host "Please install Claude Code CLI first: https://docs.anthropic.com/en/docs/claude-code" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "If you have already installed claude CLI, please check:" -ForegroundColor Yellow
            Write-Host "  1. Restart your terminal to refresh PATH" -ForegroundColor White
            Write-Host "  2. Ensure claude is in your PATH: where.exe claude" -ForegroundColor White
            Write-Host "  3. For npm install: npm install -g @anthropic-ai/claude-code" -ForegroundColor White
            exit 1
        }
    }
}

# ==============================================================================
# Step 2: Install project dependencies
# ==============================================================================
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

    # --- Windows pywin32 fix ---
    # uv sync does not execute pywin32's post-install script,
    # causing native extensions (_win32sysloader etc.) to be missing.
    # Use uv pip install --reinstall to fix this.
    $venvPython = [System.IO.Path]::Combine($PSScriptRoot, ".venv", "Scripts", "python.exe")
    if (Test-Path $venvPython) {
        & $venvPython -c "from mcp.server.fastmcp import FastMCP" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Fixing pywin32 for Windows..." -ForegroundColor Yellow
            uv pip install pywin32 --reinstall --quiet 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "pywin32 reinstall failed �?MCP may not work correctly"
            }
        }
    }
    # --- End: pywin32 fix ---

    Write-Success "Project dependencies installed"
}

# ==============================================================================
# Step 3: Register MCP server
# ==============================================================================
Write-Step "Step 3: Registering MCP server..."

if ($DryRun) {
    Write-DryRun "Would run: claude mcp remove sccg --scope user"

    # Ask user for installation method
    Write-Host ""
    Write-Host "Select installation method:"
    Write-Host "  1) Remote install (recommended) - Auto-fetches latest version from GitHub"
    Write-Host "  2) Local install - Uses current project directory (for development)"
    $installMethod = Read-Host "Enter choice [1]"
    if ([string]::IsNullOrWhiteSpace($installMethod)) { $installMethod = "1" }

    if ($installMethod -eq "2") {
        Write-DryRun "Would run: claude mcp add sccg --scope user --transport stdio -- uv run --directory `"$PSScriptRoot`" sccg-mcp"
    } else {
        # Check uv version
        $useRefresh = $false
        try {
            $uvVersionOutput = uv --version 2>&1
            if ($uvVersionOutput -match "uv (\d+)\.(\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -gt 0 -or ($major -eq 0 -and $minor -ge 4)) {
                    $useRefresh = $true
                }
            }
        } catch {}

        if ($useRefresh) {
            Write-DryRun "Would run: claude mcp add sccg --scope user --transport stdio -- uvx --refresh --from git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git sccg-mcp"
        } else {
            Write-DryRun "Would run: claude mcp add sccg --scope user --transport stdio -- uvx --from git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git sccg-mcp"
        }
    }
    Write-Success "MCP server would be registered"
} else {
    # Temporarily relax error handling for native commands in Step 3
    $oldErrorActionPreference = $ErrorActionPreference
    $oldNativeCommandEap = $null
    $ErrorActionPreference = "Continue"
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $oldNativeCommandEap = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        # Try to remove existing sccg MCP server if it exists
        $removeResult = Invoke-Claude -Arguments @("mcp","remove","sccg","--scope","user") -TimeoutSec 15
        if ($removeResult.TimedOut) {
            Write-WarningMsg "claude mcp remove timed out, skipping"
        } elseif ($removeResult.ExitCode -eq 0) {
            Write-WarningMsg "Removed existing sccg MCP server"
        }

        # Ask user for installation method
        Write-Host ""
        Write-Host "Select installation method:"
        Write-Host "  1) Remote install (recommended) - Auto-fetches latest version from GitHub"
        Write-Host "  2) Local install - Uses current project directory (for development)"
        $installMethod = Read-Host "Enter choice [1]"
        if ([string]::IsNullOrWhiteSpace($installMethod)) { $installMethod = "1" }

        if ($installMethod -eq "2") {
            # Local install: use uv run from the project directory
            $localResult = Invoke-Claude -Arguments @("mcp","add","sccg","--scope","user","--transport","stdio","--","uv","run","--directory",$PSScriptRoot,"sccg-mcp") -TimeoutSec 60
            $localSucceeded = $localResult.ExitCode -eq 0
            $localOutput = $localResult.Output

            if ($localSucceeded) {
                Write-Success "MCP server registered (local install from $PSScriptRoot)"
            } else {
                Write-ErrorMsg "Failed to register MCP server (local install)"
                Write-Host "Error details: $localOutput" -ForegroundColor Red
                exit 1
            }
        } else {
            # Remote install: existing logic with --refresh detection
            $mcpRegistered = $false
            $lastError = ""
            $useRefresh = $false
            $uvVersionKnown = $false

            try {
                $uvVersionOutput = uv --version 2>&1
                if ($uvVersionOutput -match "uv (\d+)\.(\d+)\.(\d+)") {
                    $uvVersionKnown = $true
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    # --refresh requires uv >= 0.4.0
                    if ($major -gt 0 -or ($major -eq 0 -and $minor -ge 4)) {
                        $useRefresh = $true
                    }
                }
            } catch {
                # If we can't determine version, don't use --refresh
            }

            if ($useRefresh) {
                # Try with --refresh first
                $refreshResult = Invoke-Claude -Arguments @("mcp","add","sccg","--scope","user","--transport","stdio","--","uvx","--refresh","--from","git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git","sccg-mcp") -TimeoutSec 60
                $refreshSucceeded = $refreshResult.ExitCode -eq 0
                $refreshOutput = $refreshResult.Output

                if ($refreshSucceeded) {
                    $mcpRegistered = $true
                    Write-Success "MCP server registered (with --refresh)"
                } else {
                    # Check if error is about --refresh option (covers various CLI error message formats)
                    # Use -replace to normalize whitespace for reliable matching
                    $refreshOutputStr = ($refreshOutput | Out-String) -replace '\s+', ' '
                    if ($refreshOutputStr -match "(?i)(unknown|unrecognized|unexpected|invalid|no such|unsupported|found argument).*--refresh|--refresh.*(unknown|unrecognized|unexpected|invalid|no such|unsupported|found argument)|unknown option.*--refresh") {
                        # Fallback: --refresh was rejected, try without it
                        Write-WarningMsg "--refresh option was rejected, falling back to installation without --refresh..."
                        $fallbackResult = Invoke-Claude -Arguments @("mcp","add","sccg","--scope","user","--transport","stdio","--","uvx","--from","git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git","sccg-mcp") -TimeoutSec 60
                        $fallbackSucceeded = $fallbackResult.ExitCode -eq 0
                        $fallbackOutput = $fallbackResult.Output
                        if ($fallbackSucceeded) {
                            $mcpRegistered = $true
                            Write-Success "MCP server registered (without --refresh)"
                        } else {
                            $lastError = $fallbackOutput
                        }
                    } else {
                        $lastError = $refreshOutput
                    }
                }
            } else {
                # uv version too old or unknown, skip --refresh
                if ($uvVersionKnown) {
                    Write-WarningMsg "Your uv version does not support --refresh option (requires uv >= 0.4.0)"
                } else {
                    Write-WarningMsg "Could not determine uv version, skipping --refresh option"
                }
                Write-WarningMsg "Installing without --refresh..."
                Write-WarningMsg "Consider upgrading uv: powershell -c `"irm https://astral.sh/uv/install.ps1 | iex`""

                $fallbackResult = Invoke-Claude -Arguments @("mcp","add","sccg","--scope","user","--transport","stdio","--","uvx","--from","git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git","sccg-mcp") -TimeoutSec 60
                $fallbackSucceeded = $fallbackResult.ExitCode -eq 0
                $fallbackOutput = $fallbackResult.Output
                if ($fallbackSucceeded) {
                    $mcpRegistered = $true
                    Write-Success "MCP server registered (without --refresh)"
                } else {
                    $lastError = $fallbackOutput
                }
            }

            if (-not $mcpRegistered) {
                Write-ErrorMsg "Failed to register MCP server"
                Write-Host "Error details: $lastError" -ForegroundColor Red
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

# ==============================================================================
# Step 4: Install Skills
# ==============================================================================
Write-Step "Step 4: Installing Skills..."

$skillsDir = "$env:USERPROFILE\.claude\skills"
$sccgWorkflowSource = Join-Path $PSScriptRoot "skills\sccg-workflow"
$geminiCollabSource = Join-Path $PSScriptRoot "skills\gemini-collaboration"

if ($DryRun) {
    if (!(Test-Path $skillsDir)) {
        Write-DryRun "Would create directory: $skillsDir"
    }
    if (Test-Path $sccgWorkflowSource) {
        Write-DryRun "Would copy: $sccgWorkflowSource -> $skillsDir\sccg-workflow"
        Write-Success "sccg-workflow skill would be installed"
    } else {
        Write-WarningMsg "sccg-workflow skill not found, would skip"
    }
    if (Test-Path $geminiCollabSource) {
        Write-DryRun "Would copy: $geminiCollabSource -> $skillsDir\gemini-collaboration"
        Write-Success "gemini-collaboration skill would be installed"
    } else {
        Write-WarningMsg "gemini-collaboration skill not found, would skip"
    }
} else {
    try {
        # Create skills directory if it doesn't exist
        if (!(Test-Path $skillsDir)) {
            New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null
            Write-Success "Created skills directory: $skillsDir"
        }

        # Copy sccg-workflow skill
        if (Test-Path $sccgWorkflowSource) {
            $dest = "$skillsDir\sccg-workflow"
            if (Test-Path $dest) {
                Remove-Item -Recurse -Force $dest
            }
            Copy-Item -Recurse $sccgWorkflowSource $dest
            Write-Success "Installed sccg-workflow skill"
        } else {
            Write-WarningMsg "sccg-workflow skill not found, skipping"
        }

        # Copy gemini-collaboration skill
        if (Test-Path $geminiCollabSource) {
            $dest = "$skillsDir\gemini-collaboration"
            if (Test-Path $dest) {
                Remove-Item -Recurse -Force $dest
            }
            Copy-Item -Recurse $geminiCollabSource $dest
            Write-Success "Installed gemini-collaboration skill"
        } else {
            Write-WarningMsg "gemini-collaboration skill not found, skipping"
        }
    } catch {
        Write-ErrorMsg "Failed to install skills"
        exit 1
    }
}

# ==============================================================================
# Step 5: Configure global CLAUDE.md
# ==============================================================================
Write-Step "Step 5: Configuring global CLAUDE.md..."

$claudeMdPath = "$env:USERPROFILE\.claude\CLAUDE.md"
$sccgMarker = "# SCCG Configuration"

# Read SCCG config from external file to avoid encoding issues
$sccgConfigPath = Join-Path $PSScriptRoot "templates\sccg-global-prompt.md"

if ($DryRun) {
    if (!(Test-Path $claudeMdPath)) {
        if (Test-Path $sccgConfigPath) {
            Write-DryRun "Would create: $claudeMdPath (from template)"
            Write-Success "Global CLAUDE.md would be created"
        } else {
            Write-WarningMsg "SCCG global prompt template not found at $sccgConfigPath"
        }
    } else {
        $content = Get-Content $claudeMdPath -Raw -Encoding UTF8
        if ($content -match [regex]::Escape($sccgMarker)) {
            Write-WarningMsg "SCCG configuration already exists in CLAUDE.md, would skip"
        } else {
            if (Test-Path $sccgConfigPath) {
                Write-DryRun "Would append SCCG configuration to: $claudeMdPath"
                Write-Success "SCCG configuration would be appended to CLAUDE.md"
            } else {
                Write-WarningMsg "SCCG global prompt template not found at $sccgConfigPath"
            }
        }
    }
} else {
    try {
        if (!(Test-Path $claudeMdPath)) {
            # Create new file with SCCG config
            if (Test-Path $sccgConfigPath) {
                Copy-Item $sccgConfigPath $claudeMdPath
                Write-Success "Created global CLAUDE.md"
            } else {
                Write-WarningMsg "SCCG global prompt template not found at $sccgConfigPath"
                Write-WarningMsg "Please manually copy the SCCG configuration to $claudeMdPath"
            }
        } else {
            # Check if SCCG config already exists
            $content = Get-Content $claudeMdPath -Raw -Encoding UTF8
            if ($content -match [regex]::Escape($sccgMarker)) {
                Write-WarningMsg "SCCG configuration already exists in CLAUDE.md, skipping"
            } else {
                # Append SCCG config
                if (Test-Path $sccgConfigPath) {
                    $sccgContent = Get-Content $sccgConfigPath -Raw -Encoding UTF8
                    Add-Content -Path $claudeMdPath -Value "`n$sccgContent" -Encoding UTF8
                    Write-Success "Appended SCCG configuration to CLAUDE.md"
                } else {
                    Write-WarningMsg "SCCG global prompt template not found at $sccgConfigPath"
                    Write-WarningMsg "Please manually copy the SCCG configuration to $claudeMdPath"
                }
            }
        }
    } catch {
        Write-ErrorMsg "Failed to configure global CLAUDE.md: $_"
        exit 1
    }
}

# ==============================================================================
# Step 6: Configure Coder
# ==============================================================================
Write-Step "Step 6: Configuring Coder..."

$configDir = "$env:USERPROFILE\.sccg-mcp"
$configPath = "$configDir\config.toml"

if ($DryRun) {
    if (!(Test-Path $configDir)) {
        Write-DryRun "Would create directory: $configDir"
    }
    if (Test-Path $configPath) {
        Write-WarningMsg "Config file already exists at $configPath"
        Write-DryRun "Would prompt: Overwrite? (y/N)"
    }
    Write-DryRun "Would prompt for: API Token, Base URL, Model"
    Write-DryRun "Would create config file: $configPath"
    Write-DryRun "Would set file permissions (current user only)"
    Write-Success "Coder configuration would be saved"
} else {
    $skipCoderConfig = $false

    try {
        # Create config directory if it doesn't exist
        if (!(Test-Path $configDir)) {
            New-Item -ItemType Directory -Path $configDir -Force | Out-Null
        }

        # Check if config already exists
        if (Test-Path $configPath) {
            Write-WarningMsg "Config file already exists at $configPath"
            $overwrite = Read-Host "Overwrite? (y/N)"
            if ($overwrite -ne "y" -and $overwrite -ne "Y") {
                Write-WarningMsg "Skipping Coder configuration"
                $skipCoderConfig = $true
            }
        }

        if (-not $skipCoderConfig) {
            # Prompt for API Token
            $apiToken = Read-Host "Enter your API Token"
            if ([string]::IsNullOrWhiteSpace($apiToken)) {
                Write-ErrorMsg "API Token is required"
                exit 1
            }

            # Prompt for Base URL (optional)
            $baseUrl = Read-Host "Enter Base URL (default: https://open.bigmodel.cn/api/anthropic)"
            if ([string]::IsNullOrWhiteSpace($baseUrl)) {
                $baseUrl = "https://open.bigmodel.cn/api/anthropic"
            }

            # Prompt for Model (optional)
            $model = Read-Host "Enter Model (default: glm-4.7)"
            if ([string]::IsNullOrWhiteSpace($model)) {
                $model = "glm-4.7"
            }

            # Escape special characters for TOML string values (backslash and double quote)
            $safeApiToken = $apiToken -replace '\\', '\\' -replace '"', '\"'
            $safeBaseUrl = $baseUrl -replace '\\', '\\' -replace '"', '\"'
            $safeModel = $model -replace '\\', '\\' -replace '"', '\"'

            # Generate config.toml
            $configContent = @"
[coder]
api_token = "$safeApiToken"
base_url = "$safeBaseUrl"
model = "$safeModel"

[coder.env]
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
"@

            # Use UTF8 without BOM - critical for TOML parsers
            # PowerShell 5.x's "Set-Content -Encoding UTF8" writes BOM (EF BB BF) which breaks TOML parsing
            [System.IO.File]::WriteAllText($configPath, $configContent, [System.Text.UTF8Encoding]::new($false))

            # Set file permissions - only current user can read/write
            $acl = Get-Acl $configPath
            $acl.SetAccessRuleProtection($true, $false)
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, "FullControl", "Allow")
            $acl.SetAccessRule($rule)
            Set-Acl $configPath $acl

            Write-Success "Coder configuration saved to $configPath"
        }

    } catch {
        Write-ErrorMsg "Failed to configure Coder: $_"
        exit 1
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
    Write-Host "  .\setup.ps1" -ForegroundColor White
} else {
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Success "SCCG setup completed successfully!"
    Write-Host "============================================================`n" -ForegroundColor Green

    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Restart Claude Code CLI" -ForegroundColor White
    Write-Host "  2. Verify MCP server: claude mcp list" -ForegroundColor White
    Write-Host "  3. Check available skills: /sccg-workflow" -ForegroundColor White
}
Write-Host ""
