[CmdletBinding()]
param(
  [ValidateSet('preflight', 'evaluate', 'collect')]
  [string]$Mode = 'preflight',
  [Parameter(Mandatory = $true)]
  [string]$PacketPath,
  [string]$Root,
  [string]$ControlDirectory,
  [string]$RunManifestPath,
  [switch]$DropStopEvent
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:PacketWriter = $null
$script:Sequence = 0
$script:RunId = [Guid]::NewGuid().ToString('N')
$script:StartSource = "agmsg-255-$($script:RunId)-start"
$script:StopSource = "agmsg-255-$($script:RunId)-stop"
$script:TargetPid = $null
$script:StartRecords = New-Object System.Collections.ArrayList
$script:StopRecords = New-Object System.Collections.ArrayList
$script:CollectorReason = $null

function Get-Field {
  param(
    [Parameter(Mandatory = $true)] [object]$Record,
    [Parameter(Mandatory = $true)] [string]$Name
  )
  $property = $Record.PSObject.Properties[$Name]
  if ($null -eq $property) { return $null }
  return $property.Value
}

function Write-PacketRecord {
  param([Parameter(Mandatory = $true)] [hashtable]$Record)
  if ($null -eq $script:PacketWriter) { return }
  $script:Sequence++
  $Record['sequence'] = $script:Sequence
  $Record['collector_received_time_utc'] = [DateTime]::UtcNow.ToString('o')
  $json = $Record | ConvertTo-Json -Compress -Depth 10
  $script:PacketWriter.WriteLine($json)
  $script:PacketWriter.Flush()
}

function Convert-TimeCreated {
  param([object]$Raw)
  $rawText = if ($null -eq $Raw) { '' } else { [string]$Raw }
  try {
    if ($rawText -notmatch '^[0-9]+$') { throw 'time-created-not-integer' }
    $value = [Int64]$rawText
    if ($value -le 0) { throw 'time-created-not-positive' }
    $utc = [DateTime]::FromFileTimeUtc($value)
    return [pscustomobject]@{
      raw = $rawText
      utc = $utc.ToString('o')
      quality = 'known'
    }
  } catch {
    return [pscustomobject]@{
      raw = $rawText
      utc = $null
      quality = 'unknown'
    }
  }
}

function Get-Generation {
  param([string]$ProcessId)
  if ([string]::IsNullOrEmpty($ProcessId)) { return $null }
  $matches = @($script:StartRecords | Where-Object {
      [string]$_.process_id -eq $ProcessId -and
      [string]$_.generation_quality -eq 'known'
    })
  if ($matches.Count -eq 0) { return $null }
  return $matches[$matches.Count - 1]
}

function Write-ProcessTraceRecord {
  param(
    [Parameter(Mandatory = $true)] [ValidateSet('start', 'stop')] [string]$TraceType,
    [Parameter(Mandatory = $true)] [object]$EventObject
  )

  $pidText = [string]$EventObject.ProcessID
  if ($Mode -eq 'collect') {
    $time = Convert-TimeCreated $EventObject.TIME_CREATED
    Write-PacketRecord @{
      record_type = "process-$TraceType"
      process_id = $pidText
      parent_process_id = [string]$EventObject.ParentProcessID
      process_name = [string]$EventObject.ProcessName
      event_time_created_raw = $time.raw
      event_generated_time_utc = $time.utc
      event_received_time_utc = [DateTime]::UtcNow.ToString('o')
      event_clock_quality = $time.quality
      generation_quality = 'pending-offline-evaluation'
      scope = 'pending-root-registration'
    }
    return
  }
  if ($pidText -ne [string]$script:TargetPid) { return }

  $time = Convert-TimeCreated $EventObject.TIME_CREATED
  $generation = 'unknown'
  $generationQuality = 'unknown'
  if ($TraceType -eq 'start' -and $time.quality -eq 'known') {
    $generation = "pid=$pidText;start=$($time.raw)"
    $generationQuality = 'known'
  } elseif ($TraceType -eq 'stop') {
    $start = Get-Generation $pidText
    if ($null -ne $start -and $time.quality -eq 'known') {
      $generation = [string]$start.generation
      $generationQuality = 'known'
    }
  }

  $record = @{
    record_type = "process-$TraceType"
    scope = 'fixture-owned-target'
    process_id = $pidText
    parent_process_id = [string]$EventObject.ParentProcessID
    process_name = [string]$EventObject.ProcessName
    event_generated_time_utc = $time.utc
    event_time_created_raw = $time.raw
    event_clock_quality = [string]$time.quality
    generation = $generation
    generation_quality = $generationQuality
    event_received_time_utc = [DateTime]::UtcNow.ToString('o')
  }
  if ($TraceType -eq 'stop') {
    $record['exit_status'] = [string]$EventObject.ExitStatus
  }
  Write-PacketRecord $record

  $record['collector_received_time_utc'] = [DateTime]::UtcNow.ToString('o')
  if ($TraceType -eq 'start') {
    [void]$script:StartRecords.Add([pscustomobject]$record)
  } else {
    [void]$script:StopRecords.Add([pscustomobject]$record)
  }
}

function Drain-TraceEvents {
  foreach ($source in @($script:StartSource, $script:StopSource)) {
    $events = @(Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue)
    foreach ($event in $events) {
      try {
        $newEvent = $event.SourceEventArgs.NewEvent
        if ($source -eq $script:StartSource) {
          Write-ProcessTraceRecord -TraceType start -EventObject $newEvent
        } else {
          Write-ProcessTraceRecord -TraceType stop -EventObject $newEvent
        }
      } catch {
        $script:CollectorReason = 'event-record-invalid'
        Write-PacketRecord @{
          record_type = 'quality'
          quality = 'unknown'
          reason = 'event-record-invalid'
          error_type = $_.Exception.GetType().Name
        }
      } finally {
        Remove-Event -EventIdentifier $event.EventIdentifier -ErrorAction SilentlyContinue
      }
    }
  }
}

function Register-TraceSubscriptions {
  try {
    Register-WmiEvent -Namespace 'root\cimv2' -Class Win32_ProcessStartTrace -SourceIdentifier $script:StartSource | Out-Null
    Register-WmiEvent -Namespace 'root\cimv2' -Class Win32_ProcessStopTrace -SourceIdentifier $script:StopSource | Out-Null

    $startSubscriber = @(Get-EventSubscriber -SourceIdentifier $script:StartSource -ErrorAction SilentlyContinue)
    $stopSubscriber = @(Get-EventSubscriber -SourceIdentifier $script:StopSource -ErrorAction SilentlyContinue)
    if ($startSubscriber.Count -ne 1 -or $stopSubscriber.Count -ne 1) {
      throw 'subscription-not-confirmed'
    }
    Write-PacketRecord @{
      record_type = 'subscription-ready'
      subscription_status = 'ready'
      start_source = $script:StartSource
      stop_source = $script:StopSource
      clock_source = 'Win32_ProcessStartTrace/Win32_ProcessStopTrace.TIME_CREATED'
      clock_quality = 'pending-event'
    }
    return $true
  } catch {
    $script:CollectorReason = 'subscription-failed'
    Write-PacketRecord @{
      record_type = 'quality'
      quality = 'unknown'
      reason = 'subscription-failed'
      error_type = $_.Exception.GetType().Name
    }
    return $false
  }
}

function Unregister-TraceSubscriptions {
  $verified = $true
  $hadSubscriber = $false
  foreach ($source in @($script:StartSource, $script:StopSource)) {
    try {
      $before = @(Get-EventSubscriber -SourceIdentifier $source -ErrorAction SilentlyContinue)
      if ($before.Count -gt 0) {
        $hadSubscriber = $true
        Unregister-Event -SourceIdentifier $source -ErrorAction Stop
      }
      $remaining = @(Get-EventSubscriber -SourceIdentifier $source -ErrorAction SilentlyContinue)
      if ($remaining.Count -ne 0) { $verified = $false }
    } catch {
      $verified = $false
    }
  }
  $verified = $verified -and $hadSubscriber
  if (-not $verified -and $null -eq $script:CollectorReason) {
    $script:CollectorReason = 'subscription-end-unconfirmed'
  }
  Write-PacketRecord @{
    record_type = 'subscription-ended'
    subscription_end_confirmed = $verified
    subscription_status = if ($verified) { 'ended' } else { 'unknown' }
  }
  return $verified
}

function Wait-ForTargetStart {
  param([int]$TimeoutSeconds = 15)
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    Drain-TraceEvents
    if (@($script:StartRecords | Where-Object {
        [string]$_.process_id -eq [string]$script:TargetPid
      }).Count -gt 0) {
      return $true
    }
    Start-Sleep -Milliseconds 50
  }
  return $false
}

function Wait-ForTargetStop {
  param([int]$TimeoutSeconds = 15)
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    Drain-TraceEvents
    if (@($script:StopRecords | Where-Object {
        [string]$_.process_id -eq [string]$script:TargetPid
      }).Count -gt 0) {
      return $true
    }
    Start-Sleep -Milliseconds 50
  }
  Drain-TraceEvents
  return @($script:StopRecords | Where-Object {
    [string]$_.process_id -eq [string]$script:TargetPid
  }).Count -gt 0
}

function Invoke-RecordedTaskkill {
  param(
    [Parameter(Mandatory = $true)] [int]$TargetProcessId,
    [Parameter(Mandatory = $true)] [string]$OperationId
  )
  $actorTime = [DateTime]::UtcNow
  $start = Get-Generation ([string]$TargetProcessId)
  $generation = if ($null -eq $start) { 'unknown' } else { [string]$start.generation }
  $generationQuality = if ($null -eq $start) { 'unknown' } else { [string]$start.generation_quality }
  $output = ''
  $rc = $null
  try {
    $output = (& taskkill.exe /PID $TargetProcessId /T /F 2>&1 | Out-String).Trim()
    $rc = [int]$LASTEXITCODE
  } catch {
    $output = ''
    $rc = $null
    $script:CollectorReason = 'taskkill-unavailable'
  }
  Write-PacketRecord @{
    record_type = 'taskkill'
    operation = 'taskkill'
    operation_id = $OperationId
    actor = 'isolated-preflight'
    actor_time_utc = $actorTime.ToString('o')
    process_id = [string]$TargetProcessId
    generation = $generation
    generation_quality = $generationQuality
    rc = if ($null -eq $rc) { 'unknown' } else { [string]$rc }
    success = ($null -ne $rc -and $rc -eq 0)
    output = $output
  }
  return [pscustomobject]@{
    operation_id = $OperationId
    actor_time_utc = $actorTime.ToString('o')
    process_id = [string]$TargetProcessId
    generation = $generation
    generation_quality = $generationQuality
    rc = $rc
  }
}

function Parse-Utc {
  param([object]$Value)
  try {
    if ($null -eq $Value -or [string]::IsNullOrEmpty([string]$Value)) { throw 'missing-time' }
    return ([DateTime]::Parse([string]$Value)).ToUniversalTime()
  } catch {
    return $null
  }
}

function New-UnknownSummary {
  param([string]$Reason)
  # Failure reporting never inherits the producer's generation/clock claims.
  return [ordered]@{
    evaluation_schema_version = 3
    evaluator_revision = (& git -C $PSScriptRoot rev-parse HEAD)
    source_packet_sha256 = if (Test-Path -LiteralPath $PacketPath -PathType Leaf) {
      (Get-FileHash -LiteralPath $PacketPath -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { $null }
    source_packet_schema = 'unknown'
    collector_run_id = $script:RunId
    comparison = 'unknown'
    comparison_gate = 'blocked'
    collector_quality = 'unknown'
    notification_capture_quality = 'unknown'
    clock_format_quality = 'unknown'
    process_time_quality = 'unknown'
    process_generation_quality = 'unknown'
    reasons = @($Reason, 'process-time-unproven', 'process-generation-unproven', 'scope-unproven')
    reason_details = @(@{ reason = $Reason; record_ids = @(); target = 'packet' })
    lifetime_ms = $null
    hold_ms = $null
    reaper_judgment = 'unknown'
    termination_actor = 'not-determined'
    uncaptured = 'unknown'
  }
}

function Evaluate-Records {
  param([object[]]$Records, [switch]$DropOneStop, [switch]$AsJson)
  if ($DropOneStop) { throw 'v3 controls require an explicitly modified packet copy' }
  # Evaluate the original bytes, not reserialized Records: provenance is the
  # input file hash. This shared evaluator handles both legacy and v2 packets.
  $json = & python (Join-Path $PSScriptRoot 'lifetime_packet.py') $PacketPath
  if ($LASTEXITCODE -ne 0) { throw 'offline v3 evaluation failed' }
  if ($AsJson) { return $json }
  $value = $json | ConvertFrom-Json
  $summary = [ordered]@{}
  foreach ($property in $value.PSObject.Properties) { $summary[$property.Name] = $property.Value }
  return $summary
}

function Read-Packet {
  param([Parameter(Mandatory = $true)] [string]$Path)
  $records = New-Object System.Collections.ArrayList
  foreach ($line in [IO.File]::ReadAllLines($Path)) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    try {
      [void]$records.Add(($line | ConvertFrom-Json))
    } catch {
      [void]$records.Add([pscustomobject]@{
        record_type = 'quality'
        quality = 'unknown'
        reason = 'malformed-packet-record'
      })
    }
  }
  return @($records)
}

function Write-Summary {
  param(
    [Parameter(Mandatory = $true)] [System.Collections.IDictionary]$Summary,
    [Parameter(Mandatory = $true)] [string]$Path
  )
  $json = $Summary | ConvertTo-Json -Compress -Depth 10
  [IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $script:Utf8NoBom)
  Write-Output $json
}

if ($Mode -eq 'collect') {
  . (Join-Path $PSScriptRoot 'lifetime-collect.ps1')
  $collectExit = Invoke-LifetimeCollection -ControlDirectory $ControlDirectory -RunManifestPath $RunManifestPath
  exit $collectExit
}

if ($Mode -eq 'evaluate') {
  if (-not (Test-Path -LiteralPath $PacketPath -PathType Leaf)) {
    $summary = New-UnknownSummary 'packet-unavailable'
    Write-Output ($summary | ConvertTo-Json -Compress -Depth 10)
    exit 0
  }
  $records = Read-Packet $PacketPath
  Evaluate-Records -Records $records -DropOneStop:$DropStopEvent -AsJson
  exit 0
}

$summaryPath = [IO.Path]::ChangeExtension($PacketPath, 'evaluation-v3.json')
$preflightExit = 1
$subscriptionReady = $false
$taskkillResult = $null
$child = $null

try {
  $packetDirectory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($PacketPath))
  if (-not [string]::IsNullOrEmpty($packetDirectory)) {
    New-Item -ItemType Directory -Path $packetDirectory -Force | Out-Null
  }
  if ([string]::IsNullOrEmpty($Root)) {
    throw 'fixture-root-required'
  }
  New-Item -ItemType Directory -Path $Root -Force | Out-Null
  $script:PacketWriter = [System.IO.StreamWriter]::new($PacketPath, $false, $script:Utf8NoBom)

  $subscriptionReady = Register-TraceSubscriptions
  if ($subscriptionReady) {
    $marker = "AGMSG_255_$($script:RunId)"
    $childCommand = "`$null = '$marker'; Start-Sleep -Seconds 30"
    $child = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-NonInteractive', '-Command', $childCommand) -WorkingDirectory $Root -PassThru -WindowStyle Hidden
    $script:TargetPid = [int]$child.Id
    Write-PacketRecord @{
      record_type = 'fixture-target'
      scope = 'fixture-owned-target'
      fixture_root_key = 'isolated-root'
      process_id = [string]$script:TargetPid
      marker = $marker
      start_actor = 'isolated-preflight'
    }

    if (-not (Wait-ForTargetStart)) {
      $script:CollectorReason = 'start-event-not-observed'
      Write-PacketRecord @{
        record_type = 'quality'
        quality = 'unknown'
        reason = 'start-event-not-observed'
      }
    } else {
      $operationId = "op-$($script:RunId)-taskkill"
      $taskkillResult = Invoke-RecordedTaskkill -TargetProcessId $script:TargetPid -OperationId $operationId
      if (-not (Wait-ForTargetStop)) {
        $script:CollectorReason = 'stop-event-not-observed'
        Write-PacketRecord @{
          record_type = 'quality'
          quality = 'unknown'
          reason = 'stop-event-not-observed'
        }
      }
    }
  }
} catch {
  if ($null -eq $script:CollectorReason) {
    $script:CollectorReason = 'preflight-exception'
  }
  Write-PacketRecord @{
    record_type = 'quality'
    quality = 'unknown'
    reason = [string]$script:CollectorReason
    error_type = $_.Exception.GetType().Name
  }
} finally {
  try {
    if ($null -ne $child -and $null -ne $script:TargetPid) {
      $stillRunning = $false
      try { $stillRunning = -not $child.HasExited } catch { $stillRunning = $false }
      if ($stillRunning) {
        try {
          Stop-Process -Id $script:TargetPid -Force -ErrorAction SilentlyContinue
          Write-PacketRecord @{
            record_type = 'fixture-cleanup'
            method = 'Stop-Process'
            process_id = [string]$script:TargetPid
            reason = 'preflight-fallback-only'
          }
        } catch { }
      }
    }
    Drain-TraceEvents
    [void](Unregister-TraceSubscriptions)
  } catch {
    if ($null -eq $script:CollectorReason) { $script:CollectorReason = 'subscription-end-unconfirmed' }
  }
  if ($null -ne $script:PacketWriter) {
    $script:PacketWriter.Flush()
    $script:PacketWriter.Dispose()
    $script:PacketWriter = $null
  }
}

if (-not (Test-Path -LiteralPath $PacketPath -PathType Leaf)) {
  $summary = New-UnknownSummary 'packet-unavailable'
  Write-Output ($summary | ConvertTo-Json -Compress -Depth 10)
  exit 1
}

$records = Read-Packet $PacketPath
$summary = Evaluate-Records -Records $records
# Evaluate-Records already saved the distinct v3 file without overwriting evidence.
if ([string](Get-Field ([pscustomobject]$summary) 'comparison') -eq 'known') {
  $preflightExit = 0
}
exit $preflightExit
