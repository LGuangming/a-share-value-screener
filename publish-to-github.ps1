# Run in Windows PowerShell from the extracted project folder.
# Requirements: Git and GitHub CLI (gh) installed; `gh auth login` completed.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Repo = "LGuangming/a-share-value-screener"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is not installed. Install Git for Windows first." }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "GitHub CLI (gh) is not installed. See https://cli.github.com/" }
gh auth status
if ($LASTEXITCODE -ne 0) { throw "Run gh auth login first." }
if (-not (Test-Path ".git")) { git init -b main }
git add -A
$existingCommit = git rev-parse --verify HEAD 2>$null
if ($LASTEXITCODE -ne 0) {
  git commit -m "feat: initialize A-share net-net screening MVP"
  if ($LASTEXITCODE -ne 0) { throw "Git commit failed. Set git config user.name and user.email, then retry." }
}
# The repository must not pre-exist. If it already exists, use git remote + push.
gh repo create $Repo --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw "GitHub repository creation/push failed. No existing repository was modified." }
Write-Host "Created and pushed: https://github.com/$Repo"
