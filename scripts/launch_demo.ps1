param([switch] $PC)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskConfigPath = Join-Path $taskRoot 'config\device.local.json'
if (-not (Test-Path -LiteralPath $taskConfigPath)) {
    throw 'Copy config/device.json.example to config/device.local.json and configure your devices first.'
}
$taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = (Get-Command python -ErrorAction Stop).Source }
$taskBackend = if ($PC) { 'pc' } else { [string]$taskConfig.backend }
if ($taskBackend -notin @('x5', 'pc')) { throw 'backend must be x5 or pc.' }
$taskUrl = [string]$taskConfig.esp_url
$taskUri = $null
if (-not [Uri]::TryCreate($taskUrl, [UriKind]::Absolute, [ref]$taskUri) -or $taskUri.Scheme -ne 'http' -or $taskUri.UserInfo) {
    throw 'esp_url must be an http address without credentials.'
}
$taskArgs = @('--url', $taskUrl, '--backend', $taskBackend, '--seconds', '0', '--decision', 'adaptive', '--speech-volume', '55', '--no-speech')
if ($taskConfig.esp_via_x5) {
    if ($taskBackend -ne 'x5') { throw 'esp_via_x5 requires backend x5. Use a direct ESP address for PC inference.' }
    $taskArgs += '--esp-via-x5'
}
Push-Location -LiteralPath $taskRoot
try {
    & $taskPython (Join-Path $taskRoot 'benchmarks\ground_demo_20261101\live_demo.py') @taskArgs
    $taskExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExitCode
