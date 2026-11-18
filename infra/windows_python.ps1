# Standard Windows/PowerShell bootstrap: no Python is required to run this file.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Wait-OwnDeskTask {
    param($Task)
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(60)
    while (-not $Task.IsCompleted) {
        if ([DateTime]::UtcNow -gt $taskDeadline) { throw 'Python download timed out. Retry setup.' }
        if ($script:OwnDeskBootstrapForm) {
            [System.Windows.Forms.Application]::DoEvents()
            if ($script:OwnDeskBootstrapForm.IsDisposed) { throw 'Python preparation was cancelled.' }
        }
        Start-Sleep -Milliseconds 40
    }
    return $Task.GetAwaiter().GetResult()
}

function Assert-OwnDeskDirectory {
    param([string]$Path)
    $taskDirectory = [IO.DirectoryInfo]::new([IO.Path]::GetFullPath($Path))
    while ($taskDirectory) {
        if ($taskDirectory.Exists -and ($taskDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw ('OwnDesk directories cannot contain junctions or symbolic links: ' + $taskDirectory.FullName)
        }
        $taskDirectory = $taskDirectory.Parent
    }
}

function Save-OwnDeskPythonArchive {
    param($Item, [string]$Archive, [scriptblock]$Progress)
    if ((Test-Path -LiteralPath $Archive) -and (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Item.sha256) { return }
    if (-not $Item.url.StartsWith('https://')) { throw 'Python download requires HTTPS.' }
    $taskPartial = $Archive + '.partial'
    $taskClient = [Net.Http.HttpClient]::new()
    $taskClient.Timeout = [TimeSpan]::FromSeconds(60)
    $taskResponse = $null
    $taskInput = $null
    $taskOutput = $null
    try {
        & $Progress 'Downloading Python...' 0
        $taskResponse = Wait-OwnDeskTask ($taskClient.GetAsync($Item.url, [Net.Http.HttpCompletionOption]::ResponseHeadersRead))
        $taskResponse.EnsureSuccessStatusCode() | Out-Null
        $taskInput = Wait-OwnDeskTask ($taskResponse.Content.ReadAsStreamAsync())
        $taskOutput = [IO.File]::Open($taskPartial, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $taskBuffer = [byte[]]::new(262144)
        [long]$taskCount = 0
        while ($true) {
            $taskRead = Wait-OwnDeskTask ($taskInput.ReadAsync($taskBuffer, 0, $taskBuffer.Length))
            if ($taskRead -eq 0) { break }
            $taskCount += $taskRead
            if ($taskCount -gt [long]$Item.size) { throw 'Unexpected Python archive size.' }
            $taskOutput.Write($taskBuffer, 0, $taskRead)
            & $Progress 'Downloading Python...' ([int](100 * $taskCount / [long]$Item.size))
        }
        $taskOutput.Dispose(); $taskOutput = $null
        if ($taskCount -ne [long]$Item.size -or (Get-FileHash -LiteralPath $taskPartial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Item.sha256) {
            throw 'Python archive SHA-256 verification failed.'
        }
        Move-Item -LiteralPath $taskPartial -Destination $Archive -Force
    } finally {
        if ($taskOutput) { $taskOutput.Dispose() }
        if ($taskInput) { $taskInput.Dispose() }
        if ($taskResponse) { $taskResponse.Dispose() }
        $taskClient.Dispose()
        if (Test-Path -LiteralPath $taskPartial) { Remove-Item -LiteralPath $taskPartial -Force }
    }
}

function Get-OwnDeskPython {
    param([string]$InstallRoot = (Join-Path $env:ProgramFiles 'OwnDesk\Bootstrap'), [scriptblock]$Progress = {param($Message, $Percent)})
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'OwnDesk Python preparation requires administrator rights.' }
    $taskProduct = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'OwnDesk'))
    $taskRoot = [IO.Path]::GetFullPath($InstallRoot)
    if (-not $taskRoot.StartsWith($taskProduct.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid OwnDesk bootstrap directory.' }
    Assert-OwnDeskDirectory $taskRoot
    $taskAclScript = Join-Path $PSScriptRoot 'set_directory_acl.ps1'
    New-Item -ItemType Directory -Path $taskProduct -Force | Out-Null
    & $taskAclScript -Target $taskProduct -SharedParent
    New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
    & $taskAclScript -Target $taskRoot
    $taskItem = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows-python.json') -Raw | ConvertFrom-Json
    if ($taskItem.version -notmatch '^3\.14\.\d+$' -or $taskItem.build -notmatch '^\d{8}$' -or $taskItem.sha256 -notmatch '^[0-9a-f]{64}$' -or $taskItem.filename -notmatch '^python-3\.14\.\d+-amd64\.zip$') { throw 'Invalid pinned Python metadata.' }
    $taskTarget = Join-Path $taskRoot ('python-' + $taskItem.version + '-' + $taskItem.build)
    $taskStamp = Join-Path $taskTarget 'owndesk-python-files.json'
    & $Progress 'Checking Python files...' -1
    if (Test-Path -LiteralPath $taskTarget) {
        if (-not (Test-Path -LiteralPath $taskStamp)) { throw 'Unverified Python directory exists. Restore the managed runtime before retrying.' }
        $taskVerified = Get-Content -LiteralPath $taskStamp -Raw | ConvertFrom-Json
        if ($taskVerified.source_sha256 -ne $taskItem.sha256 -or -not $taskVerified.files.PSObject.Properties) { throw 'Python verification metadata does not match this release.' }
        foreach ($taskProperty in $taskVerified.files.PSObject.Properties) {
            $taskFile = [IO.Path]::GetFullPath((Join-Path $taskTarget $taskProperty.Name))
            if (-not $taskFile.StartsWith($taskTarget + '\', [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $taskFile) -or (Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskProperty.Value) {
                throw 'Managed Python files changed. Restore the runtime before retrying.'
            }
            if ($script:OwnDeskBootstrapForm) {
                [System.Windows.Forms.Application]::DoEvents()
                if ($script:OwnDeskBootstrapForm.IsDisposed) { throw 'Python preparation was cancelled.' }
            }
        }
        return (Join-Path $taskTarget 'python.exe')
    }
    $taskArchive = Join-Path $taskRoot $taskItem.filename
    Save-OwnDeskPythonArchive $taskItem $taskArchive $Progress
    & $Progress 'Extracting Python...' -1
    $taskPackage = [IO.Compression.ZipFile]::OpenRead($taskArchive)
    try {
        foreach ($taskEntry in $taskPackage.Entries) {
            $taskName = $taskEntry.FullName
            if ($taskName.StartsWith('/') -or $taskName.Contains('\') -or $taskName.Contains(':') -or (($taskName -split '/') -contains '..') -or $taskName.IndexOf([char]0) -ge 0 -or (($taskEntry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Unsafe path in Python archive.' }
        }
    } finally { $taskPackage.Dispose() }
    $taskStaging = Join-Path $taskRoot ('.python-' + [Guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $taskStaging | Out-Null
        [IO.Compression.ZipFile]::ExtractToDirectory($taskArchive, $taskStaging)
        $taskExtracted = $taskStaging
        $taskPython = Join-Path $taskExtracted 'python.exe'
        $taskSignature = Get-AuthenticodeSignature -LiteralPath $taskPython
        if ($taskSignature.Status -ne 'Valid' -or $taskSignature.SignerCertificate.Subject -notmatch '(^|,\s*)(CN|O)=Python Software Foundation(,|$)') { throw 'Python publisher signature verification failed.' }
        & $taskPython -I -B -c 'import sys,struct,tkinter,ssl,sqlite3,venv,ensurepip; assert sys.version_info[:3] == tuple(map(int,sys.argv[1].split(chr(46)))) and struct.calcsize(chr(80)) == 8' $taskItem.version
        if ($LASTEXITCODE -ne 0) { throw 'Downloaded Python failed its compatibility check.' }
        $taskFiles = @{}
        foreach ($taskFile in Get-ChildItem -LiteralPath $taskExtracted -File -Recurse) {
            $taskRelative = $taskFile.FullName.Substring($taskExtracted.Length + 1)
            $taskFiles[$taskRelative] = (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        @{ source_sha256 = $taskItem.sha256; files = $taskFiles } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskExtracted 'owndesk-python-files.json') -Encoding UTF8
        [IO.Directory]::Move($taskStaging, $taskTarget)
        & (Join-Path $env:SystemRoot 'System32\icacls.exe') $taskTarget /reset /T /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot normalize managed Python permissions.' }
        & $taskAclScript -Target $taskTarget
    } finally {
        $taskResolved = [IO.Path]::GetFullPath($taskStaging)
        if (-not $taskResolved.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid staging cleanup path.' }
        if (Test-Path -LiteralPath $taskResolved) { Remove-Item -LiteralPath $taskResolved -Recurse -Force }
    }
    return (Join-Path $taskTarget 'python.exe')
}
