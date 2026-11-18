# Compatibility entry point for PowerShell users.
param([ValidateSet('organization','demo')][string]$Mode = 'organization', [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.runtime\venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {
    $python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (!(Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
$launchOptions = @('--mode', $Mode)
if ($OpenBrowser) { $launchOptions += '--open-browser' }
& $python (Join-Path $PSScriptRoot 'launcher.py') @launchOptions
if ($LASTEXITCODE -ne 0) { throw 'OwnDesk could not start. See .runtime/startup.log.' }
