# 日用品單抽／單顆最低單價比價與神價計算站

以 **Astro** 打造的純靜態 SEO 比價工具站（適用 Cloudflare Pages / Vercel），自動換算台灣常見箱購日用品（衛生紙、洗衣膠囊、濕紙巾、紙尿褲）的單抽／單顆／單片折算價格，並內建即時他網比價計算機與 `FAQPage` 結構化資料。

## 專案結構

- `src/data/products.json`：酷澎真實日用品與單價比價資料庫
- `src/lib/pricing.ts`：單價換算、神價判斷、分類專屬 FAQ、格式化與 JSON-LD 安全序列化工具
- `src/lib/ads.ts` & `src/components/AdSlot.astro`：全站廣告版位元件與一鍵開關設定（`off` / `preview` / `adsense`）
- `src/lib/analytics.ts`：GA4 總流量、分潤點擊（`click_affiliate`）與 Cloudflare Web Analytics 設定
- `src/pages/ads.txt.ts`：依 `PUBLIC_ADSENSE_CLIENT` 自動產生 `/ads.txt`
- `.env.example`：Gemini 與全站廣告一鍵開關環境變數範本
- `src/layouts/BaseLayout.astro`：共用的 SEO Meta / Open Graph / 頁首導覽 / 頁尾版型
- `src/pages/index.astro`：首頁分類篩選、神價篩選、關鍵字搜尋與總覽卡片牆
- `src/pages/item/[slug].astro`：Programmatic SEO 動態商品比價頁（含即時計算機、同類橫向比較表、FAQ + JSON-LD）
- `astro.config.mjs`：設定靜態輸出與 `@astrojs/sitemap`
- `public/robots.txt`：搜尋引擎爬蟲規範與 Sitemap 指標
- `scripts/`：酷澎商品網址自動解析、每日查價更新（`update_prices.py` + `price_sources.json`）與匯入工具
- `.github/workflows/daily-update.yml`：GitHub Actions 每日定時（台灣時間 08:00）查價並驗證建置後自動提交更新

## 批次解析新增商品（Python + Playwright + Gemini）

```powershell
# 0. 先切換到專案資料夾
cd C:\Users\ATE088\Documents\daily-price-calc

# 1. 安裝 Python 相依套件與 Playwright Chromium 核心
python -m pip install -r scripts/requirements.txt
python -m playwright install chromium

# 2. 設定 Gemini API Key（PowerShell，請替換為您的實際金鑰）
$env:GEMINI_API_KEY = "你的_GEMINI_API_KEY"

# 3. 編輯 scripts/urls.txt 加入商品網址（支援 "商品網址 | 分潤短網址"）
# 4. 執行解析並寫入 src/data/products.json（可加 --headed 觀察瀏覽器，或 --sync 一鍵驗證建置）
python scripts/add_products.py

# 或直接從 Coupang_Affiliate 匯出的 JSON 匯入（免 Playwright 爬蟲）
python scripts/add_products.py --from-json scripts/coupang_feed.json --sync

# 若使用同層的 Coupang_Affiliate 專案，也可直接於 Coupang_Affiliate 執行一鍵同步：
# cd ..\Coupang_Affiliate
# python sync_daily_price_calc.py --dry-run
# python sync_daily_price_calc.py
```

一鍵同步驗證建置與推送部署：

```powershell
# Windows PowerShell
.\scripts\sync_and_deploy.ps1

# Git Bash / Linux / macOS
./scripts/sync_and_deploy.sh
```

## 本機啟動預覽

> **環境需求**：Astro 6 需要 **Node.js 22+**。

```powershell
# 1. 安裝相依套件
npm install

# 2. 啟動本機開發預覽（預設 http://localhost:4321）
npm run dev

# 3. 測試正式靜態建置與 sitemap-index.xml 產生結果
npm run build
npm run preview
```

## 廣告版位一鍵開關（`<AdSlot />`）

全站已於首頁商品牆下方（`home-feed`）、商品頁計算機下方（`item-calc`）、FAQ 下方（`item-bottom`）與頁尾上方（`footer`）預留 `<AdSlot />` 元件，可透過 `.env`（或 Cloudflare Pages 環境變數）一鍵控制：

```env
PUBLIC_ADS_ENABLED="true"
PUBLIC_ADS_MODE="preview"   # off=關閉 / preview=顯示預留虛線框 / adsense=正式 AdSense
PUBLIC_ADSENSE_CLIENT="ca-pub-xxxxxxxxxxxxxxxx"
PUBLIC_ADSENSE_SLOT_HOME_FEED="1234567890"
PUBLIC_ADSENSE_SLOT_ITEM_CALC="2345678901"
PUBLIC_ADSENSE_SLOT_ITEM_BOTTOM="3456789012"
PUBLIC_ADSENSE_SLOT_FOOTER="4567890123"
```

本機快速預覽所有廣告預留版位：
```powershell
$env:PUBLIC_ADS_ENABLED="true"; $env:PUBLIC_ADS_MODE="preview"; npm run build; npm run preview
```

## 流量統計與分潤點擊追蹤（GA4）

在 `.env` 或 Cloudflare Pages 環境變數填入 `PUBLIC_GA_MEASUREMENT_ID="G-XXXXXXXXXX"`（亦可選填 `PUBLIC_CF_ANALYTICS_TOKEN`），重新建置後即會自動追蹤：

- **總瀏覽量與熱門頁面**：GA4 自動記錄 `page_view`
- **分潤按鈕點擊（`click_affiliate`）**：區分 `placement`（`home_card` 首頁卡片 vs `item_sidebar` 商品頁）、`item_slug`、`item_name`、`item_category`、`value`（售價）、`unit_price`、`is_god_tier`
- **商品頁瀏覽（`view_item`）**：可直接在 GA4「探索」報表計算各商品頁的「分潤點擊率 CTR = `click_affiliate` ÷ `view_item`」
- **比價互動（`use_calculator`、`filter_products`）**：追蹤計算機使用次數與分類篩選偏好

本機測試追蹤事件（打開瀏覽器 F12 Console 觀察 `[analytics]` log）：
```powershell
$env:PUBLIC_ANALYTICS_DEBUG="true"; npm run build; npm run preview
```

## 部署前設定提醒

1. 修改 `astro.config.mjs` 的 `site` 與 `public/robots.txt` 的 `Sitemap` 網址為您的正式網域。
2. 將 `src/data/products.json` 中的 `coupang_affiliate_url` 替換為您的實際分潤連結。
3. **Cloudflare Pages / Vercel** 建置指令設為 `npm run build`，輸出目錄設為 `dist`。
