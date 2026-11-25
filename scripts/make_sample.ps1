# Test videosu üretir: Windows'un Türkçe sesiyle (OneCore "Tolga") konuşma +
# bilinçli uzun sessizlikler, dolgu sözcükleri ve yarım kalan bir cümle (retake).
# Çıktı: samples/ornek.mp4 (1920x1080, 16:9)
param(
    [string]$Out = "$PSScriptRoot\..\samples\ornek.mp4"
)
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

function Await($op, [Type]$resultType) {
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($op))
    $task.Wait() | Out-Null
    $task.Result
}

$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.Language -eq 'tr-TR' } | Select-Object -First 1
if (-not $voice) { throw "Türkçe ses bulunamadı (Ayarlar > Zaman ve dil > Konuşma)." }
$synth.Voice = $voice

$ssml = @"
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="tr-TR">
  <break time="1200ms"/>
  Merhaba arkadaşlar, bugün size yapay zeka ile video kurgusunu anlatacağım.
  <break time="2000ms"/>
  Eee. <break time="400ms"/>
  Bu uygulama videonuzu izliyor ve
  <break time="900ms"/>
  Bu uygulama videonuzu izliyor ve gereksiz kısımları otomatik olarak kesiyor.
  <break time="1800ms"/>
  Ayrıca uzay ve galaksi görüntüleri gibi ara görüntüler de ekleyebiliyor.
  <break time="2500ms"/>
  Iıı. <break time="500ms"/>
  Altyazılar kelime kelime ekrana geliyor ve konuşmayla senkron kalıyor.
  <break time="1500ms"/>
  Videoyu beğendiyseniz abone olmayı unutmayın. Görüşmek üzere!
  <break time="1500ms"/>
</speak>
"@

$stream = Await ($synth.SynthesizeSsmlToStreamAsync($ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$size = [uint32]$stream.Size
$reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
$null = Await ($reader.LoadAsync($size)) ([uint32])
$bytes = New-Object byte[] $size
$reader.ReadBytes($bytes)

$outDir = Split-Path -Parent $Out
New-Item -ItemType Directory -Force $outDir | Out-Null
$wav = Join-Path $outDir "ornek_ses.wav"
[System.IO.File]::WriteAllBytes($wav, $bytes)

$ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $ffmpeg) { $ffmpeg = "ffmpeg" }

# Görüntü: renkli test deseni + yüzü temsil eden, yavaşça sağa kayan bir daire (yüz takibi testi için)
& $ffmpeg -hide_banner -v error -y -i $wav `
    -f lavfi -i "testsrc2=s=1920x1080:r=30" `
    -filter_complex "[1:v]drawbox=x=0:y=0:w=iw:h=ih:color=0x1b263b@0.85:t=fill,drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':text='AI Kurgu test':fontcolor=white:fontsize=48:x=60:y=60[v]" `
    -map "[v]" -map 0:a -shortest -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 160k $Out
Write-Host "Oluşturuldu: $Out"
