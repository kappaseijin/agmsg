function Send-LifetimeControl {
  param([hashtable]$Record, [string]$Directory, [string]$RunId)
  $Record['run_id']=$RunId
  $Record['actor_filetime']=[DateTime]::UtcNow.ToFileTimeUtc().ToString()
  $Record['actor_time']=[DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(9)).ToString('o')
  $id=[Guid]::NewGuid().ToString('N')
  $temp=Join-Path $Directory "$id.tmp"
  $path=Join-Path $Directory "$id.control.json"
  [IO.File]::WriteAllText($temp, ($Record | ConvertTo-Json -Compress -Depth 10), [Text.UTF8Encoding]::new($false))
  [IO.File]::Move($temp,$path)
}
