# Fetches a static, LGPL-licensed Windows ffmpeg.exe build and drops it
# into backend/vendor/, where main.js's getBundledFfmpegPath() and
# electron-builder's "backend/vendor/ffmpeg.exe" files entry both pick it
# up. Bundling ffmpeg means downloads work at full quality (merged
# video+audio) on machines that never separately installed ffmpeg
# themselves, instead of silently falling back to a lower-quality
# pre-muxed format (see downloader.py's select_format).
#
# Idempotent: skips the ~140MB download entirely if backend/vendor/ffmpeg.exe
# already exists, so this is safe to run on every `npm run dist`.
#
# Source: BtbN/FFmpeg-Builds' rolling "latest" release -- an LGPL build
# (no GPL-only components like libx264/libx265), which keeps CLIP.PULL free
# of any GPL source-distribution obligation for the bundled binary. Only
# ffmpeg.exe itself is extracted (not ffprobe.exe/ffplay.exe, which this
# app never uses) to keep the installer smaller.
$ErrorActionPreference = "Stop"

$scriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot   = Split-Path -Parent $scriptsDir
$vendorDir  = Join-Path $repoRoot "backend\vendor"
$exePath    = Join-Path $vendorDir "ffmpeg.exe"

if (Test-Path $exePath) {
    "ffmpeg.exe already present at $exePath - skipping download."
    exit 0
}

New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null

$downloadUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"
$tempZip     = Join-Path $env:TEMP "clippull-ffmpeg-$([guid]::NewGuid()).zip"
$tempExtract = Join-Path $env:TEMP "clippull-ffmpeg-$([guid]::NewGuid())"

try {
    "Downloading ffmpeg (LGPL static build, ~140MB)..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $tempZip -UserAgent "clippull-build-script"

    "Extracting ffmpeg.exe and LICENSE.txt..."
    Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force
    $extractedRoot = Get-ChildItem -Path $tempExtract -Directory | Select-Object -First 1
    Copy-Item -Path (Join-Path $extractedRoot.FullName "bin\ffmpeg.exe") -Destination $exePath -Force
    Copy-Item -Path (Join-Path $extractedRoot.FullName "LICENSE.txt") -Destination (Join-Path $vendorDir "FFMPEG_LICENSE.txt") -Force
} finally {
    Remove-Item -Path $tempZip -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
}

$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
"Fetched: $exePath ($sizeMB MB)"
