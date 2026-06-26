# Shared helpers for the worktree-isolation guard. Dot-sourced by the hook scripts + test.
# Enforces "one checkout = one agent" (see CLAUDE.md Conventions and docs/runbook.md -> Parallel work).
# Each git worktree has its own checked-out .claude/, so .claude/locks/ self-scopes per checkout;
# liveness is a timestamp heartbeat (no pids), the earliest live session owns the checkout.

$script:FreshMs = 600000  # a lock is "live" if heartbeated within 10 min; raise if sessions idle longer

function Get-NowMs {
    [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
}

function Get-LocksDir {
    # .claude/locks, resolved from this script's own location (robust to cwd / missing env var).
    $dir = Join-Path (Split-Path $PSScriptRoot -Parent) 'locks'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $dir
}

function Read-HookInput {
    # Parse the hook's stdin JSON; returns $null if empty/unparseable.
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    try { return ($raw | ConvertFrom-Json) } catch { return $null }
}

function Read-Locks {
    param([string]$LocksDir)
    $locks = @()
    foreach ($f in (Get-ChildItem -Path $LocksDir -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try { $locks += (Get-Content -Raw -LiteralPath $f.FullName | ConvertFrom-Json) } catch { }
    }
    , $locks  # leading comma: always return an array, even for 0/1 elements
}

function Write-Lock {
    param([string]$LocksDir, [string]$SessionId, [long]$CreatedAt, [long]$UpdatedAt)
    $path = Join-Path $LocksDir ("{0}.json" -f $SessionId)
    $obj = [ordered]@{ session_id = $SessionId; createdAt = $CreatedAt; updatedAt = $UpdatedAt }
    [System.IO.File]::WriteAllText($path, ($obj | ConvertTo-Json -Compress))
}

function Update-Heartbeat {
    # Ensure my lock exists (createdAt set once), refresh updatedAt to now. Returns my lock object.
    param([string]$LocksDir, [string]$SessionId, [long]$NowMs)
    $path = Join-Path $LocksDir ("{0}.json" -f $SessionId)
    $created = $NowMs
    if (Test-Path $path) {
        try { $created = [long]((Get-Content -Raw -LiteralPath $path | ConvertFrom-Json).createdAt) }
        catch { $created = $NowMs }
    }
    Write-Lock -LocksDir $LocksDir -SessionId $SessionId -CreatedAt $created -UpdatedAt $NowMs
    [pscustomobject]@{ session_id = $SessionId; createdAt = $created; updatedAt = $NowMs }
}

function Remove-StaleLocks {
    param([string]$LocksDir, [long]$NowMs, [long]$FreshMs)
    foreach ($l in (Read-Locks -LocksDir $LocksDir)) {
        if ($null -eq $l) { continue }
        if (($NowMs - [long]$l.updatedAt) -ge $FreshMs) {
            $p = Join-Path $LocksDir ("{0}.json" -f $l.session_id)
            Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-WorktreeConflict {
    # Pure decision (unit-tested): block iff another LIVE lock owns this checkout -- a different
    # session, heartbeated within FreshMs, that is older (createdAt; session_id breaks createdAt
    # ties so exactly one session ever owns and the two never deadlock-block each other).
    param($Mine, $Locks, [long]$NowMs, [long]$FreshMs)
    foreach ($l in $Locks) {
        if ($null -eq $l) { continue }
        if ($l.session_id -eq $Mine.session_id) { continue }            # ignore my own lock
        if (($NowMs - [long]$l.updatedAt) -ge $FreshMs) { continue }    # stale -> not a live owner
        $olderByTime = [long]$l.createdAt -lt [long]$Mine.createdAt
        $olderByTie = ([long]$l.createdAt -eq [long]$Mine.createdAt) -and ($l.session_id -lt $Mine.session_id)
        if ($olderByTime -or $olderByTie) { return $true }
    }
    return $false
}
