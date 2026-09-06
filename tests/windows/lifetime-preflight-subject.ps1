param([string]$Directory,[string]$RunId,[int]$ExitCode=0)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'lifetime-control.ps1')
$self=Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
Send-LifetimeControl @{record_type='root-binding'; root_key='target'; process_id=[string]$PID; creation_date=$self.CreationDate.ToString('o'); match=$true; match_basis='fixture-self'} $Directory $RunId
$null=Get-CimInstance Win32_Process -Filter "ParentProcessId=$PID"
Send-LifetimeControl @{record_type='snapshot-boundary'; phase='before'; root_key='target'} $Directory $RunId
# No hold/poll process: the child exits entirely between the two snapshots.
$child=Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','exit','0') -PassThru -WindowStyle Hidden
Send-LifetimeControl @{record_type='required-process'; process_id=[string]$child.Id; parent_process_id=[string]$PID; root_key='target'} $Directory $RunId
$child.WaitForExit()
Start-Sleep -Milliseconds 250
$null=Get-CimInstance Win32_Process -Filter "ParentProcessId=$PID"
Send-LifetimeControl @{record_type='snapshot-boundary'; phase='after'; root_key='target'} $Directory $RunId
exit $ExitCode
