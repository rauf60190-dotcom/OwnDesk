# Local owner portal: separate database and session from the organization server.
$ErrorActionPreference = 'Stop'
$portalRoot = $PSScriptRoot
$portalPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (!(Test-Path -LiteralPath $portalPython)) { $portalPython = (Get-Command python -ErrorAction Stop).Source }
$portalVenv = Join-Path $portalRoot '.runtime\venv'
$portalExe = Join-Path $portalVenv 'Scripts\python.exe'
if (!(Test-Path -LiteralPath $portalExe)) { & $portalPython -m venv $portalVenv }
& $portalExe -m pip install -q -r (Join-Path $portalRoot 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$env:DEBUG = '1'
$env:DEPLOYMENT_MODE = 'portal'
$env:DATABASE_URL = ''
$env:LOCAL_DB_PATH = Join-Path $portalRoot '.runtime\portal.sqlite3'
$env:LOCAL_CHANNELS = '1'
$env:LOCAL_TELEGRAM_POLLING = '1'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:5175'
$env:PUBLIC_REGISTRATION_ENABLED = '1'
$env:LOCAL_EMAIL_PREVIEW = '1'
$env:PYTHONUTF8 = '1'
if (!$env:PORTAL_RELEASE_DIR) { $env:PORTAL_RELEASE_DIR = Join-Path $portalRoot '.runtime\releases' }
$portalSecretFile = Join-Path $portalRoot '.runtime\portal-session.key'
if (!(Test-Path -LiteralPath $portalSecretFile)) {
    & $portalExe -c "import secrets,sys; open(sys.argv[1], 'x').write(secrets.token_urlsafe(48))" $portalSecretFile
    if ($LASTEXITCODE -ne 0) { throw 'Could not create portal session key.' }
}
$env:SECRET_KEY = [IO.File]::ReadAllText($portalSecretFile)
$portalAccountKey = Join-Path $portalRoot '.runtime\portal-security.key'
if (!(Test-Path -LiteralPath $portalAccountKey)) {
    & $portalExe -c "from cryptography.fernet import Fernet; import sys; open(sys.argv[1], 'xb').write(Fernet.generate_key())" $portalAccountKey
    if ($LASTEXITCODE -ne 0) { throw 'Could not create portal account key.' }
}
$env:ACCOUNT_ENCRYPTION_KEY = [IO.File]::ReadAllText($portalAccountKey)
Push-Location (Join-Path $portalRoot 'backend')
try {
    & $portalExe manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Migrations failed.' }
    Write-Host 'OwnDesk owner portal: http://127.0.0.1:5175. Test letters appear in the interface.'
    & $portalExe manage.py run_local --port 5175
} finally { Pop-Location }
