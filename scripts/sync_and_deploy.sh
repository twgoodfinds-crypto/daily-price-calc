#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

echo "==> 1. 執行酷澎商品解析與寫入 products.json"
"$PYTHON_BIN" scripts/add_products.py "$@"

echo "==> 2. 執行 Astro 靜態建置驗證 (npm run build)"
npm run build

if [[ "${AUTO_PUSH:-0}" == "1" ]]; then
  DO_PUSH="y"
else
  read -r -p "建置成功！是否執行 git add / commit / push 部署上線？[y/N] " DO_PUSH
fi

if [[ "$DO_PUSH" =~ ^[Yy]$ ]]; then
  if [[ -z "$(git status --porcelain -- src/data/products.json scripts/urls.txt)" ]]; then
    echo "src/data/products.json 與 scripts/urls.txt 沒有任何變更需要提交。"
    exit 0
  fi
  COMMIT_MSG="${COMMIT_MSG:-chore(data): update daily product prices ($(date +%F))}"
  git add src/data/products.json scripts/urls.txt
  git commit -m "$COMMIT_MSG"
  git push
  echo "✅ 已完成 Git 推送部署！"
else
  echo "已跳過 Git 推送。若要直接自動推送，可使用 AUTO_PUSH=1 ./scripts/sync_and_deploy.sh"
fi
