param(
    [Parameter(Mandatory = $true)]
    [string]$DockerSubnet,
    [int]$Port = 9700
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell session."
}

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$wrapper = Join-Path $base "launcher-service.exe"
$config = Join-Path $base "launcher-service.xml"
foreach ($required in @($wrapper, $config, (Join-Path $base "launcher.exe"), (Join-Path $base "launcher.env"))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing required service file: $required"
    }
}

& $wrapper install
if ($LASTEXITCODE -ne 0) {
    throw "Windows service wrapper installation failed."
}

$ruleName = "DiscordCombatAI Launcher from Docker"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress $DockerSubnet `
    -Profile Any | Out-Null

& $wrapper start
if ($LASTEXITCODE -ne 0) {
    throw "Windows service was installed but did not start."
}
