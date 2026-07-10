<#
.SYNOPSIS
  Cuts a CLIP.PULL release: bumps the version, builds the Windows installer,
  tags and pushes, then publishes a GitHub release with the .exe attached.

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

# 7. Publish the GitHub release with the installer attached.
Write-Host "Creating GitHub release $tag..." -ForegroundColor Cyan
gh release create $tag $exe.FullName --title "CLIP.PULL $tag" --notes $notes
if ($LASTEXITCODE -ne 0) { Fail "gh release create failed - version was pushed but no release was published." }

Write-Host "Released $tag." -ForegroundColor Green
