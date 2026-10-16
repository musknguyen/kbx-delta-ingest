# Full terminal demo for the take-home review.
#   1. Code overview (structure, commits, Dockerfile, README, no hard-coded keys)
#   2. Unit tests
#   3. Daily sync run (logs added / updated / skipped)
#   4. OptiBot answers a question with the cited Article URL
#   5. Docker proof: trigger the GitHub Actions job (docker build + docker run) and stream its logs
#
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1

$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

$python   = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
$hasDocker = [bool](Get-Command docker -ErrorAction SilentlyContinue)
$question = "How do I add a YouTube video?"
$repoUrl  = "https://github.com/musknguyen/kbx-delta-ingest"

function Step($title) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
    [void](Read-Host "  [Enter] to run")
}

function Show($cmd) {
    # Print the command like a prompt, then run it.
    Write-Host "PS> $cmd" -ForegroundColor Yellow
    Invoke-Expression $cmd
}

function ExitCode { Write-Host "exit code: $LASTEXITCODE" -ForegroundColor Green }

# ---------------------------------------------------------------- 1. Code overview
Step "1/5  Code overview - repo $repoUrl"
Show "git remote get-url origin"
Show "git log --oneline -10"
Show "git ls-files | Where-Object { `$_ -notlike 'data/articles/*' }"
Write-Host "(+ $((git ls-files data/articles).Count) cleaned Markdown articles in data/articles/)" -ForegroundColor DarkGray

Step "1/5  Dockerfile and .env.sample (no hard-coded keys)"
Show "Get-Content Dockerfile"
Show "Get-Content .env.sample"
Show "git check-ignore .env"
Write-Host "Searching tracked files for API keys..." -ForegroundColor DarkGray
$leak = git grep -nE "AIza[0-9A-Za-z_-]{20}|AQ\.[0-9A-Za-z_-]{20}" -- . ":!*.png" 2>$null
if ($leak) { Write-Host $leak -ForegroundColor Red } else { Write-Host "No keys found in the repo." -ForegroundColor Green }

Step "1/5  README (setup, run locally, job logs, screenshot)"
Show "Get-Content README.md"

# ---------------------------------------------------------------- 2. Tests
Step "2/5  Unit tests (HTML cleaner + delta logic)"
Show "& $python -m pytest -v"
ExitCode

# ---------------------------------------------------------------- 3. Sync
if ($hasDocker) {
    $key = (Get-Content .env | Where-Object { $_ -match '^(GEMINI_API_KEY|API_KEY)=' } | Select-Object -First 1) -replace '^[^=]+=', ''
    Step "3/5  docker build + docker run -e API_KEY=... kb-sync main.py"
    Show "docker build -t kb-sync ."
    Write-Host "PS> docker run --rm -e API_KEY=*** kb-sync main.py" -ForegroundColor Yellow
    docker run --rm -e "API_KEY=$key" kb-sync main.py
    ExitCode
} else {
    Step "3/5  Daily sync: scrape -> Markdown -> delta upload (logs added/updated/skipped)"
    Show "& $python main.py"
    ExitCode
}

# ---------------------------------------------------------------- 4. Assistant
Step "4/5  OptiBot answers: `"$question`""
if ($hasDocker) {
    Write-Host "PS> docker run --rm -e API_KEY=*** kb-sync main.py ask `"$question`"" -ForegroundColor Yellow
    docker run --rm -e "API_KEY=$key" kb-sync main.py ask $question
} else {
    Show "& $python main.py ask `"$question`""
}
ExitCode

# ---------------------------------------------------------------- 5. Docker in CI
Step "5/5  Daily job on GitHub Actions: tests -> docker build -> docker run (live)"
Show "gh workflow run daily-sync.yml"
Start-Sleep -Seconds 6
$runId = gh run list --workflow daily-sync.yml -L 1 --json databaseId -q ".[0].databaseId"
Write-Host "Run: $repoUrl/actions/runs/$runId" -ForegroundColor DarkGray
Show "gh run watch $runId --exit-status"

Write-Host ""
Write-Host "Docker steps and counts from the job log:" -ForegroundColor Cyan
gh run view $runId --log 2>$null |
    Select-String -Pattern "docker build -t|docker run --rm|naming to docker.io|articles_scraped|added  |updated  |skipped  |deleted  |failed  " |
    ForEach-Object { ($_.Line -split "\t")[-1] -replace '^\S+Z ', '' -replace '(\x1b|\^\[)\[[0-9;]*m', '' -replace '^##\[group\]', '' } |
    Select-Object -Unique

Show "gh run view $runId"
Write-Host ""
Write-Host "Demo finished." -ForegroundColor Green
