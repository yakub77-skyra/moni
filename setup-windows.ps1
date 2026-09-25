# OpenMontage Windows Setup Script
# Run this to set up the Python environment and install dependencies

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\USER\.cline\data\workspaces\chat\OpenMontage"
$VenvPath = Join-Path $ProjectRoot ".venv"

Write-Host "==> Setting up OpenMontage..." -ForegroundColor Cyan
Write-Host ""

# Create virtual environment
Write-Host "==> Creating virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path $VenvPath)) {
    py -3 -m venv $VenvPath
}
Write-Host "    Virtual environment created at: $VenvPath" -ForegroundColor Green

# Activate and install
Write-Host ""
Write-Host "==> Installing Python dependencies..." -ForegroundColor Yellow
& $VenvPath\Scripts\Activate.ps1
python -m pip install --upgrade pip -q
python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Host ""
Write-Host "==> Installing Remotion composer (Node.js dependencies)..." -ForegroundColor Yellow
Set-Location $ProjectRoot
Push-Location (Join-Path $ProjectRoot "remotion-composer")
npm install
Pop-Location

Write-Host ""
Write-Host "==> Installing Piper TTS (offline text-to-speech)..." -ForegroundColor Yellow
python -m pip install piper-tts -q

Write-Host ""
Write-Host "==> Setting up .env file..." -ForegroundColor Yellow
$EnvExample = Join-Path $ProjectRoot ".env.example"
$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "    Created .env from .env.example" -ForegroundColor Green
} else {
    Write-Host "    .env already exists, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==> Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Add your API keys to .env (optional)"
Write-Host "  2. Open this project in your AI coding assistant"
Write-Host "  3. Tell it what video you want to create!"
Write-Host ""
Write-Host "Example: \"Make a 60-second animated explainer about how neural networks learn\""
