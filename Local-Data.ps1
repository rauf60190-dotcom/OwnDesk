# Run the snapshot tool with Python 3.12 from the installation or PATH.
param(
    [Parameter(Mandatory, Position=0)][ValidateSet('backup','restore','upgrade')][string]$Action,
    [Parameter(Mandatory, Position=1)][string]$Path,
    [ValidateSet('organization','portal')][string]$Profile = 'organization',
    [string]$FromInstallation
)
$ErrorActionPreference = 'Stop'
$snapshotPython = Join-Path $PSScriptRoot '.runtime\venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $snapshotPython)) {
    $snapshotPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (!(Test-Path -LiteralPath $snapshotPython)) {
    $snapshotPython = (Get-Command python -ErrorAction Stop).Source
}
$snapshotArgs = @($Action, $Path, '--profile', $Profile)
if ($FromInstallation) { $snapshotArgs += @('--from-installation', $FromInstallation) }
& $snapshotPython (Join-Path $PSScriptRoot 'backend\local_snapshot.py') @snapshotArgs
if ($LASTEXITCODE -ne 0) { throw "OwnDesk data operation failed (exit code $LASTEXITCODE)." }
