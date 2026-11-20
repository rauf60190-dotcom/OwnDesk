param([switch]$DisclosureAccepted)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    if (-not $DisclosureAccepted) {
        $taskNotice = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'INSTALLATION-NOTICE.txt'), [Text.Encoding]::UTF8)
        $taskConsent = [System.Windows.Forms.MessageBox]::Show($taskNotice, 'OwnDesk — перед подготовкой', 'OKCancel', 'Information', 'Button2')
        if ($taskConsent -ne 'OK') { exit 0 }
    }
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'OwnDesk Server requires Windows x86-64.'
    }
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $taskArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -DisclosureAccepted'
        $taskElevated = Start-Process -FilePath 'powershell.exe' -ArgumentList $taskArgs -Verb RunAs -WindowStyle Hidden -PassThru -Wait
        exit $taskElevated.ExitCode
    }
    $taskRoot = Split-Path $PSScriptRoot -Parent
    . (Join-Path $PSScriptRoot 'windows_python.ps1')
    $script:OwnDeskBootstrapForm = [System.Windows.Forms.Form]::new()
    $script:OwnDeskBootstrapForm.Text = 'OwnDesk — подготовка'
    $script:OwnDeskBootstrapForm.Size = [Drawing.Size]::new(660, 260)
    $script:OwnDeskBootstrapForm.StartPosition = 'CenterScreen'
    $script:OwnDeskBootstrapForm.FormBorderStyle = 'FixedDialog'
    $script:OwnDeskBootstrapForm.MaximizeBox = $false
    $script:OwnDeskBootstrapForm.BackColor = [Drawing.Color]::FromArgb(11, 18, 32)
    $taskLabel = [System.Windows.Forms.Label]::new()
    $taskLabel.Location = [Drawing.Point]::new(28, 30)
    $taskLabel.Size = [Drawing.Size]::new(590, 65)
    $taskLabel.ForeColor = [Drawing.Color]::White
    $taskLabel.Font = [Drawing.Font]::new('Segoe UI', 13)
    $taskLabel.Text = 'Подготавливаем OwnDesk. При первом запуске скачивается проверенная среда Python.'
    $taskProgress = [System.Windows.Forms.ProgressBar]::new()
    $taskProgress.Location = [Drawing.Point]::new(28, 125)
    $taskProgress.Size = [Drawing.Size]::new(590, 24)
    $script:OwnDeskBootstrapForm.Controls.Add($taskLabel)
    $script:OwnDeskBootstrapForm.Controls.Add($taskProgress)
    $script:OwnDeskBootstrapForm.Show()
    [System.Windows.Forms.Application]::DoEvents()
    $taskPython = Get-OwnDeskPython -Progress {
        param([string]$Message, [int]$Percent)
        $taskLabel.Text = switch ($Message) {
            'Downloading Python...' { 'Скачиваем Python…' }
            'Checking Python files...' { 'Проверяем файлы Python…' }
            'Extracting Python...' { 'Распаковываем Python…' }
            default { $Message }
        }
        if ($Percent -lt 0) { $taskProgress.Style = 'Marquee' }
        else { $taskProgress.Style = 'Continuous'; $taskProgress.Value = [Math]::Min(100, $Percent) }
        [System.Windows.Forms.Application]::DoEvents()
    }
    $script:OwnDeskBootstrapForm.Dispose()
    $script:OwnDeskBootstrapForm = $null
    $taskPythonw = Join-Path (Split-Path $taskPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $taskPythonw)) { $taskPythonw = $taskPython }
    $taskWindow = Join-Path $PSScriptRoot 'windows_server_window.py'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $taskLogRoot = Join-Path $taskRoot '.runtime'
    New-Item -ItemType Directory -Path $taskLogRoot -Force | Out-Null
    $taskProcess = Start-Process -FilePath $taskPythonw -ArgumentList ('-B "' + $taskWindow + '"') -WorkingDirectory $taskRoot -WindowStyle Normal -RedirectStandardOutput (Join-Path $taskLogRoot 'server-window.out.log') -RedirectStandardError (Join-Path $taskLogRoot 'server-window.err.log') -PassThru -Wait
    if ($taskProcess.ExitCode -ne 0) { throw ('OwnDesk setup window failed. See ' + (Join-Path $taskLogRoot 'server-window.err.log')) }
    exit $taskProcess.ExitCode
} catch {
    $taskFailure = $_
    try {
        $taskErrorRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.runtime'
        New-Item -ItemType Directory -Path $taskErrorRoot -Force | Out-Null
        ($taskFailure.Exception.Message + [Environment]::NewLine + $taskFailure.ScriptStackTrace) | Set-Content -LiteralPath (Join-Path $taskErrorRoot 'bootstrap-error.log') -Encoding UTF8
    } catch {}
    if ($script:OwnDeskBootstrapForm) { $script:OwnDeskBootstrapForm.Dispose() }
    [System.Windows.Forms.MessageBox]::Show($taskFailure.Exception.Message, 'OwnDesk Server', 'OK', 'Error') | Out-Null
    exit 1
}
