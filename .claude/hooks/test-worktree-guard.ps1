# Self-check of Test-WorktreeConflict (the pure decision behind the worktree guard).
# Run: powershell -NoProfile -File .claude/hooks/test-worktree-guard.ps1   (exit 1 on any failure)
. (Join-Path $PSScriptRoot 'worktree-lib.ps1')

$now = 1000000
$fresh = 600000
$script:fail = 0

function Check([string]$name, [bool]$expected, [bool]$actual) {
    if ($expected -eq $actual) { Write-Host "PASS  $name" }
    else { Write-Host "FAIL  $name (expected $expected, got $actual)"; $script:fail++ }
}

$mine = [pscustomobject]@{ session_id = 'me'; createdAt = 5000; updatedAt = $now }

# 1. solo: only my own lock -> allow
Check 'solo allows' $false (Test-WorktreeConflict -Mine $mine -Locks @($mine) -NowMs $now -FreshMs $fresh)

# 2. older, live other -> block
$older = [pscustomobject]@{ session_id = 'a'; createdAt = 1000; updatedAt = $now }
Check 'older live other blocks' $true (Test-WorktreeConflict -Mine $mine -Locks @($mine, $older) -NowMs $now -FreshMs $fresh)

# 3. older but STALE other -> allow (no live owner)
$stale = [pscustomobject]@{ session_id = 'a'; createdAt = 1000; updatedAt = ($now - $fresh - 1) }
Check 'stale other allows' $false (Test-WorktreeConflict -Mine $mine -Locks @($mine, $stale) -NowMs $now -FreshMs $fresh)

# 4. newer other (I am the owner) -> allow
$newer = [pscustomobject]@{ session_id = 'a'; createdAt = 9000; updatedAt = $now }
Check 'newer other allows (I own)' $false (Test-WorktreeConflict -Mine $mine -Locks @($mine, $newer) -NowMs $now -FreshMs $fresh)

# 5. my own session id is ignored even if it looks older -> allow
$dupSelf = [pscustomobject]@{ session_id = 'me'; createdAt = 1; updatedAt = $now }
Check 'self ignored' $false (Test-WorktreeConflict -Mine $mine -Locks @($dupSelf) -NowMs $now -FreshMs $fresh)

# 6. createdAt tie -> lexicographically smaller session_id owns
$tieLower = [pscustomobject]@{ session_id = 'aaa'; createdAt = 5000; updatedAt = $now }   # 'aaa' < 'me' -> other owns
Check 'tie: smaller id owns (block)' $true (Test-WorktreeConflict -Mine $mine -Locks @($mine, $tieLower) -NowMs $now -FreshMs $fresh)
$tieHigher = [pscustomobject]@{ session_id = 'zzz'; createdAt = 5000; updatedAt = $now }  # 'zzz' > 'me' -> I own
Check 'tie: larger id yields (allow)' $false (Test-WorktreeConflict -Mine $mine -Locks @($mine, $tieHigher) -NowMs $now -FreshMs $fresh)

if ($script:fail -gt 0) { Write-Host "`n$($script:fail) check(s) FAILED"; exit 1 }
Write-Host "`nAll checks passed"; exit 0
