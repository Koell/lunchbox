# One-time setup for the git module: include the shared aliases from the global git config.
# Safe to run again; other include.path entries are left alone.

Set-StrictMode -Version 2.0

$default = '~/.config/git/aliases'
$path = if ($env:XDG_CONFIG_HOME) {
    (Join-Path $env:XDG_CONFIG_HOME 'git/aliases').Replace('\', '/')
} else {
    $default
}

if (-not (Get-Command -Name git -CommandType Application -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('bento: git not found')
    exit 127
}

$existing = @(git config --global --get-all include.path 2>$null)
if ($existing -contains $path) {
    Write-Output "include.path already set: $path"
} else {
    # PowerShell 7 on Windows expands a leading ~ in native arguments, even from a variable,
    # which would store this machine's home path. Only a quoted literal is passed unchanged.
    if ($path -eq $default) {
        git config --global --add include.path '~/.config/git/aliases'
    } else {
        git config --global --add include.path $path
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output "added include.path: $path"
}

$file = if ($path.StartsWith('~/')) { Join-Path $HOME $path.Substring(2) } else { $path }
if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
    Write-Output "note: $file does not exist yet; run python -m bento deploy git"
}
exit 0
