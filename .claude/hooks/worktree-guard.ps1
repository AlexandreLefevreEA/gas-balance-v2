# PreToolUse guard (Edit|Write|NotebookEdit): block file edits when another LIVE Claude session
# already owns this checkout, so two agents never collide in one working tree.
# Fail-OPEN: any error allows the edit -- a guard bug must never brick editing; the documented
# rule (CLAUDE.md Conventions / docs/runbook.md -> Parallel work) is the backstop.
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'worktree-lib.ps1')

    $in = Read-HookInput
    $sid = if ($in) { [string]$in.session_id } else { '' }
    if ([string]::IsNullOrWhiteSpace($sid)) { exit 0 }  # can't identify the session -> allow

    $now = Get-NowMs
    $locksDir = Get-LocksDir
    $mine = Update-Heartbeat -LocksDir $locksDir -SessionId $sid -NowMs $now   # heartbeat first
    $locks = Read-Locks -LocksDir $locksDir

    if (Test-WorktreeConflict -Mine $mine -Locks $locks -NowMs $now -FreshMs $script:FreshMs) {
        $branch = 'main'
        try { $b = (& git rev-parse --abbrev-ref HEAD 2>$null); if ($b) { $branch = "$b".Trim() } } catch { }
        $msg = @"
BLOCKED: another live Claude session already owns this checkout.
Two agents in one working tree collide -- a git branch isolates commits, not files.
Work in your OWN git worktree instead:
  git worktree add ../gas-balance-v2-<task> -b <new-branch>   # off $branch
  cd ../gas-balance-v2-<task>
then retry the edit there. For subagents, pass isolation:'worktree'.
See docs/runbook.md -> Parallel work.
"@
        [Console]::Error.WriteLine($msg)
        exit 2   # exit 2 => PreToolUse blocks the tool and feeds stderr back to the agent
    }
    exit 0
} catch {
    exit 0   # fail-open
}
