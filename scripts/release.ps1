<#
.SYNOPSIS
  Cuts a CLIP.PULL release: bumps the version, builds the Windows installer,
  tags and pushes, then publishes the installer to one persistent GitHub
  release (tag "release") under a stable, version-less filename.

.DESCRIPTION
  Every version still gets its own "v<version>" git tag for source history
  (useful for tracing which commit a bug report's build came from), but the
  GitHub Release page itself stays singular: the "release" tag is a fixed
  slug, not a version pointer, and its one asset (CLIP.PULL.Setup.exe) is
  replaced in place each time. Nothing that links to the download (the
  website) ever needs to change to pick up a new version.

.PARAMETER Bump
  Semantic Versioning bump type: "patch" (bug fixes), "minor" (new,
  backward-compatible features), or "major" (breaking changes).

.EXAMPLE
  scripts\release.ps1 -Bump patch
  scripts\release.ps1 -Bump minor
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("patch", "minor", "major")]
    [string]$Bump
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Fail($message) {
    Write-Error $message
    exit 1
}

# 1. Guard rails: refuse to release from a dirty tree, a non-master
#    branch, or a master that's behind origin.
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne "master") {
    Fail "Must be on master to release (currently on '$branch')."
}
if (git status --porcelain) {
    Fail "Working tree is not clean. Commit or stash changes before releasing."
}
git fetch origin master --quiet
if ((git rev-parse HEAD) -ne (git rev-parse origin/master)) {
    Fail "Local master is not up to date with origin/master. Pull first."
}

# 2. Bump package.json, commit, and tag: atomic via npm.
#    REMINDER: backend/version_info.txt's FileVersion/ProductVersion aren't
#    auto-synced with package.json's version (PyInstaller's --version-file
#    resource, baked into clippull-backend.exe at build time in step 3 below)
#    -- update it by hand to match $version before releasing, or it'll drift.
Write-Host "Bumping version ($Bump)..." -ForegroundColor Cyan
npm version $Bump -m "chore: release v%s"
if ($LASTEXITCODE -ne 0) { Fail "npm version failed." }
$version = (Get-Content package.json | ConvertFrom-Json).version
$tag = "v$version"
Write-Host "New version: $tag" -ForegroundColor Green

# 3. Build the installer with the new version baked in.
Write-Host "Building (backend exe, extension zip, installer)..." -ForegroundColor Cyan
npm run dist
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed - rolling back the version bump commit and tag." -ForegroundColor Yellow
    git tag -d $tag | Out-Null
    git reset --hard HEAD~1 | Out-Null
    Fail "Build failed. Version bump rolled back; nothing was pushed or released."
}

# 4. Find the built installer (there should be exactly one for this version).
$exe = Get-ChildItem -Path "dist" -Filter "*.exe" |
    Where-Object { $_.Name -like "*$version*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $exe) {
    Fail "No installer matching version $version found in dist/ after build."
}
$sizeMB = [math]::Round($exe.Length / 1MB, 1)
Write-Host "Built: $($exe.Name) ($sizeMB MB)" -ForegroundColor Green

# 5. Push the release commit and tag.
Write-Host "Pushing to origin..." -ForegroundColor Cyan
git push origin master
git push origin $tag

# 6. Release notes: every commit since the previous tag (excluding this
#    release's own version-bump commit), newest first. Works whether or
#    not this repo uses PRs, unlike GitHub's PR-based --generate-notes.
$previousTag = git describe --tags --abbrev=0 "$tag^" 2>$null
if ($previousTag) {
    $notes = (git log "$previousTag..$tag~1" --pretty=format:"- %s") -join "`n"
} else {
    $notes = (git log "$tag~1" --pretty=format:"- %s") -join "`n"
}
if (-not $notes) { $notes = "No notable changes recorded." }

# 7. Publish to the one persistent GitHub release (tag "release") rather
#    than creating a new release per version. "release" is a fixed slug,
#    not a version pointer - it does not move to track $tag, since nothing
#    downstream needs it to. The asset filename is version-less too, so the
#    website's download link never needs to change. gh's "file#label" syntax
#    only sets a cosmetic display label, NOT the actual stored filename (that
#    always comes from the local file's own name) - so the rename has to
#    happen on disk, via a real copy, before upload.
$assetName = "CLIP.PULL.Setup.exe"
$stableAssetPath = Join-Path $exe.Directory.FullName $assetName
Copy-Item -Path $exe.FullName -Destination $stableAssetPath -Force

Write-Host "Publishing $assetName on the 'release' GitHub release..." -ForegroundColor Cyan
gh release view release *> $null
if ($LASTEXITCODE -ne 0) {
    gh release create release $stableAssetPath --title "CLIP.PULL $tag" --notes $notes
    if ($LASTEXITCODE -ne 0) { Fail "gh release create failed - version was pushed but no release was published." }
} else {
    gh release upload release $stableAssetPath --clobber
    if ($LASTEXITCODE -ne 0) { Fail "gh release upload failed - version was pushed but the asset wasn't updated." }
    gh release edit release --title "CLIP.PULL $tag" --notes $notes
    if ($LASTEXITCODE -ne 0) { Fail "gh release edit failed - asset uploaded but the title/notes weren't updated." }
}

Write-Host "Released $tag (published as $assetName on the 'release' GitHub release)." -ForegroundColor Green
