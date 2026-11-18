param(
    [Parameter(Mandatory=$true)][string]$Target,
    [switch]$Writable,
    [ValidateSet('S-1-5-19', 'S-1-5-20')][string]$ServiceSid = 'S-1-5-19',
    [switch]$SharedParent,
    [switch]$AdminOnly
)
$ErrorActionPreference = 'Stop'
if (($SharedParent -and $Writable) -or ($AdminOnly -and ($Writable -or $SharedParent))) {
    throw 'Shared parents cannot be writable; admin-only directories cannot grant service access.'
}
$taskAcl = [System.Security.AccessControl.DirectorySecurity]::new()
$taskAdmin = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$taskAcl.SetOwner($taskAdmin)
$taskAcl.SetAccessRuleProtection($true, $false)
$taskInheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$taskPropagation = [System.Security.AccessControl.PropagationFlags]::None
$taskAllow = [System.Security.AccessControl.AccessControlType]::Allow
foreach ($taskSid in @('S-1-5-18', 'S-1-5-32-544')) {
    $taskIdentity = [System.Security.Principal.SecurityIdentifier]::new($taskSid)
    $taskRule = [System.Security.AccessControl.FileSystemAccessRule]::new($taskIdentity, 'FullControl', $taskInheritance, $taskPropagation, $taskAllow)
    $taskAcl.AddAccessRule($taskRule)
}
$taskRights = if ($Writable) { 'Modify' } else { 'ReadAndExecute' }
if (-not $AdminOnly) {
    $taskServiceSids = if ($SharedParent) { @('S-1-5-19', 'S-1-5-20') } else { @($ServiceSid) }
    foreach ($taskServiceSid in $taskServiceSids) {
        $taskService = [System.Security.Principal.SecurityIdentifier]::new($taskServiceSid)
        $taskAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($taskService, $taskRights, $taskInheritance, $taskPropagation, $taskAllow))
    }
}
Set-Acl -LiteralPath $Target -AclObject $taskAcl
