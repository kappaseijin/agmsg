# Dot-sourced only by process-lifetime-collector.ps1. Schema v2 is observational:
# it never starts or kills a subject process and never appends from another actor.
function Invoke-LifetimeCollection {
  param([string]$ControlDirectory, [string]$RunManifestPath)
  $ready = $false
  $seen = New-Object 'System.Collections.Generic.HashSet[string]'
  try {
    $manifest = Get-Content -LiteralPath $RunManifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne 2 -or -not $manifest.run_id) { throw 'invalid-manifest' }
    $script:RunId = [string]$manifest.run_id
    $script:StartSource = "agmsg-255-$($script:RunId)-start"
    $script:StopSource = "agmsg-255-$($script:RunId)-stop"
    if (Test-Path -LiteralPath $PacketPath) { throw 'packet-already-exists' }
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($PacketPath))) -Force | Out-Null
    New-Item -ItemType Directory -Path $ControlDirectory -Force | Out-Null
    $script:PacketWriter = [IO.StreamWriter]::new($PacketPath, $false, $script:Utf8NoBom)
    $record = @{}
    foreach ($p in $manifest.PSObject.Properties) { $record[$p.Name] = $p.Value }
    $record['record_type'] = 'run-manifest'
    Write-PacketRecord $record
    $ready = Register-TraceSubscriptions
    if (-not $ready) { return 1 }
    $readyPath = Join-Path $ControlDirectory 'ready.json'
    [IO.File]::WriteAllText("$readyPath.tmp", (@{run_id=$script:RunId; status='ready'} | ConvertTo-Json -Compress), $script:Utf8NoBom)
    [IO.File]::Move("$readyPath.tmp", $readyPath)
    $deadline = [DateTime]::UtcNow.AddSeconds([int]$manifest.collector_deadline_s)
    $stopping = $false
    while (-not $stopping -and [DateTime]::UtcNow -lt $deadline) {
      Drain-TraceEvents
      foreach ($file in @(Get-ChildItem -LiteralPath $ControlDirectory -Filter '*.control.json' | Sort-Object Name)) {
        if (-not $seen.Add($file.Name)) { continue }
        try {
          $message = [IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json
          if ($message.run_id -ne $script:RunId) { throw 'wrong-run' }
          $entry=@{}
          foreach ($p in $message.PSObject.Properties) { $entry[$p.Name]=$p.Value }
          Write-PacketRecord $entry
          if ($message.record_type -eq 'stop-request') { $stopping=$true }
        } catch {
          Write-PacketRecord @{record_type='quality'; reason='invalid-control-message'; quality='unknown'}
        }
      }
      if (-not $stopping) { Start-Sleep -Milliseconds 20 }
    }
    if (-not $stopping) {
      Write-PacketRecord @{record_type='quality'; reason='collector-deadline'; quality='unknown'}
    }
    # Drain after subject cleanup; this is a finite observation tail, never a
    # claim that WMI delivered every OS event. Missing required stops stay unknown.
    $tail=[DateTime]::UtcNow.AddSeconds(2)
    while ([DateTime]::UtcNow -lt $tail) { Drain-TraceEvents; Start-Sleep -Milliseconds 20 }
    return 0
  } catch {
    Write-PacketRecord @{record_type='quality'; reason='collection-exception'; error_type=$_.Exception.GetType().Name; quality='unknown'}
    Write-Error $_ -ErrorAction Continue
    return 1
  } finally {
    if ($null -ne $script:PacketWriter) {
      Drain-TraceEvents
      [void](Unregister-TraceSubscriptions)
      Drain-TraceEvents
      Write-PacketRecord @{record_type='packet-close'; run_id=$script:RunId}
      $script:PacketWriter.Dispose()
      $script:PacketWriter=$null
    }
  }
}
