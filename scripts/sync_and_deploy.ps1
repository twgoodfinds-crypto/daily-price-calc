param(
  [switch]$Push,
  [switch]$Headed,
  [switch]$Manual,
  [switch]$Force,
  [string]$CommitMessage = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$pyArgs = @("scripts/add_products.py")
if ($Headed) { $pyArgs += "--headed" }
if ($Manual) { $pyArgs += "--manual" }
if ($Force)  { $pyArgs += "--force" }

Write-Host "==> 1. 執行酷澎商品解析與寫入 products.json" -ForegroundColor Cyan
python @pyArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 2. 執行 Astro 靜態建置驗證 (npm run build)" -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$doPush = $Push.IsPresent
if (-not $doPush) {
  $reply = Read-Host "建置成功！是否執行 git add / commit / push 部署上線？[y/N]"
  $doPush = ($reply -match '^[Yy]$')
}

if ($doPush) {
  $status = git status --porcelain -- src/data/products.json scripts/urls.txt
  if (-not $status) {
    Write-Host "src/data/products.json 與 scripts/urls.txt 沒有任何變更需要提交。"
    exit 0
  }
  if (-not $CommitMessage) {
    $today = Get-Date -Format "yyyy-MM-dd"
    $CommitMessage = "chore(data): update daily product prices ($today)"
  }
  git add src/data/products.json scripts/urls.txt
  git commit -m $CommitMessage
  git push
  Write-Host "✅ 已完成 Git 推送部署！" -ForegroundColor Green
} else {
  Write-Host "已跳過 Git 推送。若要一鍵自動推送，可執行 .\scripts\sync_and_deploy.ps1 -Push"
}
