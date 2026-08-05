param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9_-]+$")]
    [string]$StreamName,

    [string]$MediaMtxHost = "127.0.0.1",

    [ValidateRange(320, 7680)]
    [int]$MaxWidth = 1920,

    [ValidateRange(0, 51)]
    [int]$Crf = 23,

    [ValidatePattern("^[1-9][0-9]*[KMG]$")]
    [string]$MaxRate = "8M",

    [ValidatePattern("^[1-9][0-9]*[KMG]$")]
    [string]$RateControlBuffer = "16M",

    [switch]$NoLoop
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg is not available on PATH."
}

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$outputUrl = "rtsp://${MediaMtxHost}:8554/${StreamName}"
$videoFilter = "fps=24,scale='min(iw,$MaxWidth)':-2"
$arguments = @("-hide_banner", "-re")

if (-not $NoLoop) {
    $arguments += @("-stream_loop", "-1")
}

$arguments += @(
    "-i", $resolvedInput,
    "-an",
    "-vf", $videoFilter,
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-tune", "zerolatency",
    "-profile:v", "main",
    "-pix_fmt", "yuv420p",
    "-crf", $Crf.ToString(),
    "-maxrate", $MaxRate,
    "-bufsize", $RateControlBuffer,
    "-g", "24",
    "-keyint_min", "24",
    "-sc_threshold", "0",
    "-bf", "0",
    "-fps_mode", "cfr",
    "-f", "rtsp",
    "-rtsp_transport", "tcp",
    $outputUrl
)

Write-Host "Publishing 24 FPS CFR stream to $outputUrl"
Write-Host "Video limits: max width ${MaxWidth}px, CRF $Crf, maxrate $MaxRate, buffer $RateControlBuffer"
Write-Host "LL-HLS playlist: http://${MediaMtxHost}:8888/${StreamName}/index.m3u8"
& ffmpeg @arguments
