param([string]$Directory,[string]$RunId,[string]$Root,[string]$RootKey,[string]$ControllerPid,[string]$MsysPid)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'lifetime-control.ps1')
try {
  if ($ControllerPid) {
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$ControllerPid"
    Send-LifetimeControl @{record_type='controller-binding'; process_id=[string]$process.ProcessId; creation_date=$process.CreationDate.ToString('o'); msys_pid=$MsysPid; namespace='native'; mapping_quality='known'} $Directory $RunId
    exit 0
  }
  $needles=@($Root.Replace('\','/').ToLowerInvariant())
  $matches=@(Get-CimInstance Win32_Process | Where-Object {
    $line=[string]$_.CommandLine
    $line=$line.Replace('\','/').ToLowerInvariant()
    $line.Contains('codex-bridge.js') -and $line.Contains($needles[0])
  })
  foreach ($process in $matches) {
    Send-LifetimeControl @{record_type='root-binding'; root_key=$RootKey; process_id=[string]$process.ProcessId; creation_date=$process.CreationDate.ToString('o'); match=$true; match_basis='codex-bridge.js-and-root'} $Directory $RunId
  }
  if ($matches.Count -eq 0) {
    Send-LifetimeControl @{record_type='root-observation'; root_key=$RootKey; match=$false; reason='no-current-root-match'} $Directory $RunId
  }
} catch {
  Send-LifetimeControl @{record_type='quality'; reason='root-binding-failed'; quality='unknown'; error_type=$_.Exception.GetType().Name} $Directory $RunId
  exit 1
}
