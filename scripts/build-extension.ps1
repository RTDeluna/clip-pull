# Builds a clean, loadable package of the CLIP.PULL Course Downloader
# extension and drops it into assets/extension/, where Clip.Pull's Extension
# tab (via the get-extension-package-info / save-extension-package IPC
# handlers) and electron-builder's "assets/**/*" files allowlist both pick
# it up. findExtensionZip() (main.js) matches any *.zip in that folder, so
# this filename isn't load-bearing -- it's just for humans browsing the dir.
# Includes only the files the extension needs — no docs, git, or OS junk.
# Entry paths use forward slashes (ZIP spec / Chrome requirement).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$scriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot   = Split-Path -Parent $scriptsDir
$extRoot    = Join-Path $repoRoot "extension"

$manifest = Get-Content "$extRoot\manifest.json" -Raw | ConvertFrom-Json
$version  = $manifest.version
$distDir  = Join-Path $repoRoot "assets\extension"
$zipPath  = Join-Path $distDir "clippull-course-downloader-$version.zip"

# Exact file list that makes up the shippable extension
$files = @(
  "manifest.json",
  "background.js",
  "content.js",
  "popup.html",
  "popup.js",
  "Icons/icon16.png",
  "Icons/icon48.png",
  "Icons/icon128.png"
)

New-Item -ItemType Directory -Path $distDir -Force | Out-Null

# Clear any previously built zip(s) — e.g. from a prior version — so the
# Extension tab never offers a stale package.
Get-ChildItem -Path $distDir -Filter "*.zip" -ErrorAction SilentlyContinue | Remove-Item -Force

$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
  foreach ($rel in $files) {
    $full = Join-Path $extRoot ($rel -replace '/', '\')
    if (-not (Test-Path $full)) { throw "Missing required file: $rel" }
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
      $zip, $full, $rel, [System.IO.Compression.CompressionLevel]::Optimal)
  }
} finally {
  $zip.Dispose()
}

"Built: $zipPath"
"{0:N1} KB" -f ((Get-Item $zipPath).Length / 1KB)
