param(
    [string]$Archive = (Join-Path $PSScriptRoot '../../../OwnDesk.zip'),
    [string]$Metadata = (Join-Path $PSScriptRoot '../../../release.json'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../../../installer-candidate')
)
$ErrorActionPreference='Stop'
$buildArchive=(Resolve-Path -LiteralPath $Archive).Path
$buildMeta=Get-Content -LiteralPath $Metadata -Raw | ConvertFrom-Json
if($buildMeta.version -notmatch '^\d+\.\d+\.\d+$' -or $buildMeta.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid release metadata' }
if((Get-FileHash -LiteralPath $buildArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $buildMeta.sha256) { throw 'Archive hash mismatch' }
if((Get-Item -LiteralPath $buildArchive).Length -ne $buildMeta.size) { throw 'Archive size mismatch' }
$buildCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$buildCompilerSignature=Get-AuthenticodeSignature -LiteralPath $buildCompiler
if($buildCompilerSignature.Status -ne 'Valid' -or $buildCompilerSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Windows C# compiler signature is not valid' }
if(Test-Path -LiteralPath $OutputDirectory) { throw 'Use a fresh output directory; accepted candidates are never overwritten' }
$buildOutput=New-Item -ItemType Directory -Path $OutputDirectory
$buildGenerated=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Setup.cs') -Raw).Replace('@@VERSION@@',$buildMeta.version).Replace('@@SHA256@@',$buildMeta.sha256)
$buildSource=Join-Path $buildOutput.FullName 'Setup.generated.cs'
[IO.File]::WriteAllText($buildSource,$buildGenerated,[Text.UTF8Encoding]::new($true))
$buildExe=Join-Path $buildOutput.FullName ('OwnDesk-Setup-'+$buildMeta.version+'.exe')
$buildArguments=@('/nologo','/target:winexe','/platform:x64','/optimize+',('/out:'+$buildExe),('/win32manifest:'+(Join-Path $PSScriptRoot 'app.manifest')),('/resource:'+$buildArchive+',OwnDesk.Payload'),'/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll','/reference:System.Windows.Forms.dll','/reference:System.IO.Compression.dll','/reference:System.Web.Extensions.dll',$buildSource)
& $buildCompiler @buildArguments
if($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$buildVerified=Join-Path $buildOutput.FullName 'payload-verified.json'
try {
    $buildProcess=Start-Process -FilePath $buildExe -ArgumentList @('--verify',('"'+$buildVerified+'"')) -WindowStyle Hidden -PassThru -Wait
} catch {
    @{version=$buildMeta.version;file=$buildExe;sha256=(Get-FileHash -LiteralPath $buildExe -Algorithm SHA256).Hash.ToLowerInvariant();payload_sha256=$buildMeta.sha256;payload_verified=$false;signature=(Get-AuthenticodeSignature -LiteralPath $buildExe).Status.ToString();candidate_only=$true;public_release_approved=$false;execution_error=$_.Exception.Message} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $buildOutput.FullName 'installer-candidate.json') -Encoding UTF8
    throw
}
if($buildProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $buildVerified)) { throw 'Compiled payload verification failed' }
$buildReport=Get-Content -LiteralPath $buildVerified -Raw | ConvertFrom-Json
if(!$buildReport.success -or $buildReport.payload_sha256 -ne $buildMeta.sha256) { throw 'Embedded payload does not match release' }
$buildSignature=Get-AuthenticodeSignature -LiteralPath $buildExe
@{version=$buildMeta.version;file=$buildExe;sha256=(Get-FileHash -LiteralPath $buildExe -Algorithm SHA256).Hash.ToLowerInvariant();size=(Get-Item -LiteralPath $buildExe).Length;payload_sha256=$buildMeta.sha256;payload_verified=$true;signature=$buildSignature.Status.ToString();candidate_only=$true;public_release_approved=$false;remaining_checks=@('Trusted publisher signature and timestamp','Authenticode verification of final signed bytes','Real GUI/UAC launch from a downloaded file','Clean Windows Defender and SmartScreen acceptance')} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $buildOutput.FullName 'installer-candidate.json') -Encoding UTF8
Get-Content -LiteralPath (Join-Path $buildOutput.FullName 'installer-candidate.json')
