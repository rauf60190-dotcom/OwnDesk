# Compatibility entry point for existing shortcuts and automation.
param([ValidateSet('organization','demo')][string]$Mode = 'organization')
& (Join-Path $PSScriptRoot 'Start-OwnDesk.ps1') -Mode $Mode
