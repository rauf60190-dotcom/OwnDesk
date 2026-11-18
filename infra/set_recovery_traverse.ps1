param(
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][ValidateSet('S-1-5-19','S-1-5-20')][string]$ServiceSid
)
$ErrorActionPreference = 'Stop'
$taskAcl = Get-Acl -LiteralPath $Target
if (-not $taskAcl.AreAccessRulesProtected) { throw 'Recovery parent must have a protected ACL.' }
$taskIdentity = [Security.Principal.SecurityIdentifier]::new($ServiceSid)
# This directory only: initdb needs to stat every parent. No rule is inherited by private files.
$taskRule = [Security.AccessControl.FileSystemAccessRule]::new(
    $taskIdentity, [Security.AccessControl.FileSystemRights]::ReadAndExecute,
    [Security.AccessControl.InheritanceFlags]::None,
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow)
$taskAcl.SetAccessRule($taskRule)
Set-Acl -LiteralPath $Target -AclObject $taskAcl
