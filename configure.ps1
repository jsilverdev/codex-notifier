[CmdletBinding()]
param(
    [string]$ConfigPath = $(
        if ($env:CODEX_NOTIFIER_CONFIG) { $env:CODEX_NOTIFIER_CONFIG }
        elseif ($env:LOCALAPPDATA) {
            Join-Path $env:LOCALAPPDATA 'CodexNotifier\config.json'
        }
        else { Join-Path $HOME '.codex-notifier\config.json' }
    ),
    [string]$WebhookUrl = $(
        if ($env:CODEX_TEAMS_WEBHOOK_URL) { $env:CODEX_TEAMS_WEBHOOK_URL }
        else {
            $userValue = [Environment]::GetEnvironmentVariable(
                'CODEX_TEAMS_WEBHOOK_URL', 'User'
            )
            if ($userValue) { $userValue }
            else {
                [Environment]::GetEnvironmentVariable(
                    'CODEX_TEAMS_WEBHOOK_URL', 'Machine'
                )
            }
        }
    ),
    [double]$TeamsMinimumSeconds = 120,
    [double]$VoiceMinimumSeconds = 300,
    [string]$QuietStart = '22:00',
    [string]$QuietEnd = '08:00',
    [ValidateSet('auto', 'es', 'en')]
    [string]$Language = 'auto',
    [string]$SpanishVoice = 'Microsoft Helena Desktop',
    [string]$EnglishVoice = 'Microsoft Zira Desktop',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

if ((Test-Path -LiteralPath $ConfigPath) -and -not $Force) {
    throw "Ya existe $ConfigPath. Edítalo directamente o usa -Force para reemplazarlo."
}
if ($TeamsMinimumSeconds -lt 0 -or $VoiceMinimumSeconds -lt 0) {
    throw 'Los umbrales de tiempo no pueden ser negativos.'
}
if ($QuietStart -notmatch '^([01]\d|2[0-3]):[0-5]\d$' -or
    $QuietEnd -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
    throw 'quiet_start y quiet_end deben usar el formato HH:mm.'
}

$parent = Split-Path -Parent $ConfigPath
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}

$document = [ordered]@{
    teams = [ordered]@{
        enabled = $true
        minimum_seconds = $TeamsMinimumSeconds
        webhook_url = $WebhookUrl
    }
    voice = [ordered]@{
        enabled = $true
        minimum_seconds = $VoiceMinimumSeconds
        quiet_start = $QuietStart
        quiet_end = $QuietEnd
        language = $Language
        spanish_voice = $SpanishVoice
        english_voice = $EnglishVoice
    }
}

$json = $document | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($ConfigPath, "$json`r`n", [Text.UTF8Encoding]::new($false))

Write-Host "Configuración escrita en: $ConfigPath"
if (-not $WebhookUrl) {
    Write-Warning 'El webhook está vacío. Añádelo al archivo antes de usar Teams.'
}
