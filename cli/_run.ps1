# Internal runner for the cli/*.ps1 entry scripts: finds Python 3.11+ and runs
# `python -m lunchbox <command> [args...]` from the repo root.
# Usage: & _run.ps1 <command> [args...]
# Works on Windows PowerShell 5.1 and PowerShell 7+.

Set-StrictMode -Version 2.0

$root = Split-Path -Parent $PSScriptRoot
$passThrough = @($args | ForEach-Object { [string]$_ })

$candidates = @(
    @{ Name = 'py'; Prefix = @('-3'); Label = 'py -3' },
    @{ Name = 'python'; Prefix = @(); Label = 'python' },
    @{ Name = 'python3'; Prefix = @(); Label = 'python3' }
)
$probe = "import sys; sys.stdout.write('%d.%d.%d' % sys.version_info[:3])"

$found = @()
foreach ($candidate in $candidates) {
    $command = Get-Command -Name $candidate.Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $command) { continue }
    $exe = $command.Source
    $prefix = $candidate.Prefix

    # Always probe with arguments: the Microsoft Store stub opens the Store when
    # run without any, and fails (exit 9009) when run with them.
    $output = $null
    try {
        $output = & $exe @prefix -c $probe 2>$null
    } catch {
        continue
    }
    if ($LASTEXITCODE -ne 0) { continue }
    $version = ([string]($output -join '')).Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+$') { continue }

    if ([version]$version -ge [version]'3.11') {
        Push-Location -LiteralPath $root
        try {
            & $exe @prefix -m lunchbox @passThrough
            $code = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        exit $code
    }
    $found += "$($candidate.Label) $version"
}

if ($found.Count -gt 0) {
    [Console]::Error.WriteLine("lunchbox: Python 3.11+ required, found: $($found -join ', ')")
    exit 1
}
[Console]::Error.WriteLine('lunchbox: Python 3.11+ required, found: none')
exit 127
