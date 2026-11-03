param([ValidatePattern('^[a-z0-9_]+$')][string] $OutputName='audio',[ValidateRange(-2,5)][int] $Rate=3)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$taskOutput = Join-Path $PSScriptRoot $OutputName
if (Test-Path -LiteralPath $taskOutput) { throw 'Audio directory already exists; preserve it before regeneration.' }
New-Item -ItemType Directory -Path $taskOutput | Out-Null
$taskPhrases = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'speech_phrases.json'),[System.Text.Encoding]::UTF8) | ConvertFrom-Json
$taskSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $taskVoice = @($taskSynth.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN'})[0]
    if (-not $taskVoice) { throw 'No installed zh-CN speech voice.' }
    $taskSynth.SelectVoice($taskVoice.VoiceInfo.Name)
    $taskSynth.Rate = $Rate
    $taskSynth.Volume = 100
    $taskFormat = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($taskItem in $taskPhrases) {
        $taskPath = Join-Path $taskOutput ($taskItem.direction + '.wav')
        $taskSynth.SetOutputToWaveFile($taskPath,$taskFormat)
        $taskSynth.Speak([string]$taskItem.text)
        $taskSynth.SetOutputToNull()
    }
    [pscustomobject]@{voice=$taskVoice.VoiceInfo.Name;rate=$Rate;volume=100;sample_rate=24000;playback_performed=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput 'generation.json') -Encoding UTF8
} finally {$taskSynth.Dispose()}
