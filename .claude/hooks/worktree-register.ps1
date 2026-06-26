# SessionStart: register/refresh this session's lock and reap stale locks. Side-effect only
# (SessionStart can't block). Sets createdAt at the true session start so ownership ordering is
# correct; the guard re-heartbeats on every edit. Fail-open.
try {
    . (Join-Path $PSScriptRoot 'worktree-lib.ps1')

    $in = Read-HookInput
    $sid = if ($in) { [string]$in.session_id } else { '' }
    if ([string]::IsNullOrWhiteSpace($sid)) { exit 0 }

    $now = Get-NowMs
    $locksDir = Get-LocksDir
    Remove-StaleLocks -LocksDir $locksDir -NowMs $now -FreshMs $script:FreshMs
    [void](Update-Heartbeat -LocksDir $locksDir -SessionId $sid -NowMs $now)
    exit 0
} catch {
    exit 0
}
