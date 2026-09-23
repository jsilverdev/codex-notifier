[CmdletBinding()]
param(
    [string]$CodexHome = $(
        if ($env:CODEX_HOME) { $env:CODEX_HOME }
        else { Join-Path $HOME '.codex' }
    )
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$notifier = Join-Path $projectRoot 'notifier.py'
$config = Join-Path $CodexHome 'config.toml'
$hooks = Join-Path $CodexHome 'hooks.json'

if (-not (Test-Path -LiteralPath $notifier)) {
    throw "No se encontró $notifier"
}
if (-not (Test-Path -LiteralPath $config)) {
    throw "No se encontró $config"
}
if (Test-Path -LiteralPath $hooks) {
    throw "Ya existe $hooks. Combina el hook UserPromptSubmit manualmente para no sobrescribirlo."
}
if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    throw 'No se encontró mise. Ajusta install.ps1 o configura los comandos con tu ejecutable de Python.'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item -LiteralPath $config -Destination "$config.$stamp.bak"

$tomlPath = $notifier.Replace('\', '\\').Replace('"', '\"')
$notifyLine = "notify = [`"mise`", `"exec`", `"--`", `"python`", `"$tomlPath`", `"notify`"]"
$configText = Get-Content -Raw -LiteralPath $config
if ($configText -match '(?m)^\s*notify\s*=.*$') {
    $configText = [regex]::Replace(
        $configText,
        '(?m)^\s*notify\s*=.*$',
        $notifyLine,
        1
    )
}
else {
    $configText = "$notifyLine`r`n$configText"
}
[IO.File]::WriteAllText($config, $configText, [Text.UTF8Encoding]::new($false))

$hookCommand = "mise exec -- python `"$notifier`" record-start"
$hookDocument = [ordered]@{
    description = 'Record Codex turn start times for filtered notifications.'
    hooks = [ordered]@{
        UserPromptSubmit = @(
            [ordered]@{
                hooks = @(
                    [ordered]@{
                        type = 'command'
                        command = $hookCommand
                        timeout = 5
                    }
                )
            }
        )
    }
}
$hookJson = $hookDocument | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($hooks, "$hookJson`r`n", [Text.UTF8Encoding]::new($false))

Write-Host "Configuración actualizada: $config"
Write-Host "Hook creado: $hooks"
Write-Host "Copia de seguridad: $config.$stamp.bak"
Write-Host 'Abre un nuevo chat de Codex y usa /hooks para revisar y confiar en el hook.'
