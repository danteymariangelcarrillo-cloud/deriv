<#
.SYNOPSIS
    Arranca el dashboard QuantLab en Windows (PowerShell 5.1 y 7+).

.DESCRIPTION
    Carga .env si existe, busca un puerto libre a partir de QUANTLAB_PORT
    (8050 por defecto) y ejecuta `uv run python -m quantlab`. Los logs JSON de
    structlog salen por consola. No escribe archivos.

.EXAMPLE
    .\scripts\run.ps1
    .\scripts\run.ps1 -Port 8060
#>
[CmdletBinding()]
param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $index = $line.IndexOf("=")
        if ($index -lt 1) { return }
        $name = $line.Substring(0, $index).Trim()
        $value = $line.Substring($index + 1).Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Test-PortFree {
    param([int]$Candidate)
    try {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback, $Candidate
        )
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

Import-DotEnv (Join-Path $repoRoot ".env")

if ($Port -le 0) {
    $envPort = 0
    if ([int]::TryParse($env:QUANTLAB_PORT, [ref]$envPort) -and $envPort -gt 0) {
        $Port = $envPort
    } else {
        $Port = 8050
    }
}

$selected = $Port
while ($selected -lt ($Port + 25) -and -not (Test-PortFree $selected)) {
    Write-Host "Puerto $selected ocupado; probando $($selected + 1)..."
    $selected++
}
if (-not (Test-PortFree $selected)) {
    throw "No se encontró un puerto libre entre $Port y $($Port + 24)."
}

$env:QUANTLAB_PORT = "$selected"
$env:QUANTLAB_HOST = if ($env:QUANTLAB_HOST) { $env:QUANTLAB_HOST } else { "127.0.0.1" }

Write-Host "QuantLab: http://$($env:QUANTLAB_HOST):$selected"
Write-Host "Health:  http://$($env:QUANTLAB_HOST):$selected/api/health"

Push-Location $repoRoot
try {
    uv run python -m quantlab
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
