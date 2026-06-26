# SessionEnd: drop this session's lock so the checkout frees immediately on a clean exit.
# (Crashes/hard kills don't fire SessionEnd -> the FreshMs heartbeat timeout reaps those.) Fail-open.
try {
    . (Join-Path $PSScriptRoot 'worktree-lib.ps1')

    $in = Read-HookInput
    $sid = if ($in) { [string]$in.session_id } else { '' }
    if ([string]::IsNullOrWhiteSpace($sid)) { exit 0 }

    $path = Join-Path (Get-LocksDir) ("{0}.json" -f $sid)
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    exit 0
} catch {
    exit 0
}
