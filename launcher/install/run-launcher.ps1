$ErrorActionPreference = "Stop"

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$environmentFile = Join-Path $base "launcher.env"
$launcher = Join-Path $base "launcher.exe"

if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw "Missing launcher.env beside the service wrapper."
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Missing launcher.exe beside the service wrapper."
}

foreach ($line in Get-Content -LiteralPath $environmentFile) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
        continue
    }
    $parts = $trimmed.Split("=", 2)
    if ($parts.Count -ne 2 -or $parts[0] -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
        throw "Invalid launcher.env entry."
    }
    [Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}

& $launcher daemon
exit $LASTEXITCODE
