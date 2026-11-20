param(
    [Parameter(Mandatory=$true)][string]$PostgresRoot,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference='Stop'
$signatureRoot=(Resolve-Path -LiteralPath $PostgresRoot).Path
if(Test-Path -LiteralPath $Output){throw 'Use a fresh signature report'}
foreach($requiredFile in @('bin\postgres.exe','bin\pg_ctl.exe','bin\libpq.dll','lib\plpgsql.dll')) {
    if(!(Test-Path -LiteralPath (Join-Path $signatureRoot $requiredFile) -PathType Leaf)){throw 'PostgreSQL runtime is incomplete'}
}
$signatureFiles=@()
foreach($signatureDirectory in @('bin','lib')) {
    Get-ChildItem -LiteralPath (Join-Path $signatureRoot $signatureDirectory) -Recurse -File |
        Where-Object {$_.Extension -in @('.exe','.dll')} | ForEach-Object {
            $signature=Get-AuthenticodeSignature -LiteralPath $_.FullName
            $signatureFiles+=@{file=$_.FullName.Substring($signatureRoot.Length+1);status=[string]$signature.Status;
                publisher=$signature.SignerCertificate.Subject;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
        }
}
$signatureFailures=@($signatureFiles | Where-Object {$_.status -ne 'Valid' -or $_.publisher -notmatch '(^|,\s*)CN=EnterpriseDB Corporation(,|$)'})
$signatureReport=@{success=($signatureFiles.Count -gt 0 -and $signatureFailures.Count -eq 0);files_checked=$signatureFiles.Count;files=$signatureFiles}
$signatureReport | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Output -Encoding UTF8
if(!$signatureReport.success){throw 'PostgreSQL publisher signatures did not pass verification'}
Write-Output ('Verified PostgreSQL publisher signatures: '+$signatureFiles.Count)
