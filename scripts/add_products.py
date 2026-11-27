#!/usr/bin/env python3
"""酷澎商品網址自動解析、AI 規格拆解與寫入 src/data/products.json 的工具腳本。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import dotenv_values
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URLS_FILE = Path(__file__).resolve().parent / "urls.txt"
PRODUCTS_JSON_PATH = PROJECT_ROOT / "src" / "data" / "products.json"
URL_MAP_PATH = Path(__file__).resolve().parent / ".url_map.json"

ALLOWED_CATEGORIES = (
    "衛生紙",
    "洗衣膠囊",
    "濕紙巾",
    "紙尿褲",
    "清潔劑",
    "其他日用品",
)

TRACKING_QUERY_PARAMS = {
    "sourceType",
    "src",
    "spec",
    "addtag",
    "ctag",
    "lptag",
    "itime",
    "pageType",
    "pageValue",
    "wPcid",
    "wRef",
    "wTime",
    "redirect",
    "landing",
    "mcid",
    "campaignid",
}

BLOCK_INDICATORS = (
    "access denied",
    "robot or human",
    "verify you are human",
    "are you a human",
    "cf-challenge",
    "captcha",
    "please enable js",
    "請進行驗證",
    "異常流量",
)

DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class ParsedProduct(BaseModel):
    """Gemini Structured Outputs 對應的日用品規格 Schema。"""

    slug: str = Field(
        description="全小寫英文連字號格式，包含品牌、關鍵型號與規格數字，例如 ariel-4d-laundry-pods-60"
    )
    category: Literal[
        "衛生紙",
        "洗衣膠囊",
        "濕紙巾",
        "紙尿褲",
        "清潔劑",
        "其他日用品",
    ] = Field(description="商品分類")
    brand: str = Field(description="品牌名稱（繁體中文或常見英文品牌，簡潔為主）")
    name: str = Field(
        description="乾淨的商品名稱與包裝規格（繁體中文），不可在開頭重複加上 brand 品牌名稱"
    )
    unit_name: str = Field(description="計價單位，如：抽、顆、片、毫升、克")
    pack_count: int = Field(ge=1, description="包數/盒數/瓶數（整數，單一包裝填 1）")
    units_per_pack: float = Field(ge=0.0001, description="每包內的抽數/顆數/片數/容量數字（必須大於 0）")
    total_units: float = Field(
        ge=0.0001, description="總計價單位數（必須大於 0，且等於 pack_count * units_per_pack）"
    )
    regular_price: int = Field(ge=1, description="推估的一般通路原價（整數，需高於目前特價）")
    coupang_price: int = Field(ge=1, description="擷取到的目前酷澎售價（整數，必須大於 0）")
    god_tier_unit_price: float = Field(
        ge=0.0001, description="台灣市場公認的神價門檻單價（浮點數，小數點後 3~4 位，必須大於 0）"
    )
    features: list[str] = Field(
        min_length=1,
        max_length=5,
        description="3 個口語化、寫出實際體感或小缺點的商品觀察（台灣網友口吻，禁止電商公關套話）",
    )
    best_for: str = Field(
        description="一句台灣日常口吻說明實際合拍的使用情境或族群（避免寫「適合追求高性價比的家庭」）"
    )
    editor_note: str = Field(
        description="1～2 句台灣省錢版／租屋族口吻的買前實話筆記（說明單價體感、值不值得囤、存放或核對規格提醒）"
    )

    @field_validator("units_per_pack", "total_units", "god_tier_unit_price")
    @classmethod
    def _validate_positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("數值必須大於 0")
        return value


@dataclass(frozen=True)
class UrlEntry:
    """urls.txt 中的單筆設定。"""

    product_url: str
    affiliate_url: str


@dataclass(frozen=True)
class ScrapeResult:
    """網頁擷取結果。"""

    success: bool
    raw_text: str = ""
    error: str = ""


def normalize_url(url: str) -> str:
    """標準化網址並移除追蹤參數，保留決定規格的參數（如 itemId / vendorItemId）。"""
    cleaned = url.strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k not in TRACKING_QUERY_PARAMS
    ]
    filtered_query.sort(key=lambda pair: pair[0])
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            "",
            urlencode(filtered_query),
            "",
        )
    )


def parse_price_int(raw_price: Any) -> int | None:
    """從數字或字串（如 '1,099'、'NT$ 749'）解析正整數售價。"""
    if isinstance(raw_price, bool):
        return None
    if isinstance(raw_price, (int, float)):
        val = int(round(raw_price))
        return val if val > 0 else None
    if isinstance(raw_price, str):
        digits = re.sub(r"[^\d.]", "", raw_price)
        if not digits:
            return None
        try:
            val = int(round(float(digits)))
            return val if val > 0 else None
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class FeedItem:
    """從 Coupang_Affiliate 等外部 JSON 匯入的商品原始資料。"""

    entry: UrlEntry
    raw_text: str
    product_id: str = ""
    coupang_price: int | None = None


def parse_feed_json_file(feed_path: Path) -> list[FeedItem]:
    """讀取 Coupang_Affiliate 產出的 JSON（陣列或單一物件），免爬蟲直接送 AI 拆解。"""
    if not feed_path.exists():
        raise FileNotFoundError(f"找不到匯入 JSON 檔案：{feed_path}")

    payload = json.loads(feed_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            records = payload["items"]
        elif isinstance(payload.get("products"), list):
            records = payload["products"]
        else:
            records = [payload]
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("JSON 根節點必須是物件、陣列，或包含 items/products 陣列。")

    feed_items: list[FeedItem] = []
    for idx, rec in enumerate(records, start=1):
        if not isinstance(rec, dict):
            print(f"⚠️  第 {idx} 筆資料不是 JSON 物件，已略過。")
            continue

        product_id = str(rec.get("product_id") or rec.get("productId") or "").strip()
        title = str(rec.get("product_name") or rec.get("name") or rec.get("title") or "").strip()
        raw_price = (
            rec.get("coupang_price")
            or rec.get("price")
            or rec.get("formatted_price")
            or rec.get("product_price")
        )
        parsed_price = parse_price_int(raw_price)
        affiliate_url = str(
            rec.get("affiliate_short_url")
            or rec.get("coupang_affiliate_url")
            or rec.get("short_url")
            or rec.get("affiliate_url")
            or ""
        ).strip()
        product_url = str(
            rec.get("product_url")
            or rec.get("url")
            or affiliate_url
            or (f"https://www.tw.coupang.com/vp/products/{product_id}" if product_id else "")
        ).strip()
        if not affiliate_url:
            affiliate_url = product_url

        if not title or not product_url.startswith(("http://", "https://")):
            print(f"⚠️  第 {idx} 筆缺少商品名稱或有效網址，已略過。")
            continue

        category_hint = str(rec.get("category_name") or rec.get("category") or "").strip()
        extra_spec = str(rec.get("description") or rec.get("spec_text") or "").strip()
        price_line = f"目前酷澎售價：NT${parsed_price}" if parsed_price else f"價格資訊：{raw_price}"

        raw_lines = [
            f"【商品 ID】{product_id or '未提供'}",
            f"【商品名稱與規格】{title}",
            f"【{price_line}】",
        ]
        if category_hint:
            raw_lines.append(f"【原始分類提示】{category_hint}")
        if extra_spec:
            raw_lines.append(f"【補充說明】{extra_spec}")

        feed_items.append(
            FeedItem(
                entry=UrlEntry(product_url=product_url, affiliate_url=affiliate_url),
                raw_text="\n".join(raw_lines),
                product_id=product_id,
                coupang_price=parsed_price,
            )
        )

    return feed_items


def parse_urls_file(urls_path: Path) -> list[UrlEntry]:
    """讀取 urls.txt，支援「商品網址」或「商品網址 | 分潤短網址」格式。"""
    if not urls_path.exists():
        raise FileNotFoundError(f"找不到網址清單檔案：{urls_path}")

    entries: list[UrlEntry] = []
    for line_no, raw_line in enumerate(urls_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|", maxsplit=1)]
        product_url = parts[0]
        affiliate_url = parts[1] if len(parts) > 1 and parts[1] else product_url

        if not product_url.startswith(("http://", "https://")):
            print(f"⚠️  第 {line_no} 行不是有效網址，已略過：{line}")
            continue
        if not affiliate_url.startswith(("http://", "https://")):
            print(f"⚠️  第 {line_no} 行分潤連結格式無效，改用商品原網址：{affiliate_url}")
            affiliate_url = product_url

        entries.append(UrlEntry(product_url=product_url, affiliate_url=affiliate_url))

    return entries


def load_json_file(path: Path, default: Any) -> Any:
    """安全讀取 JSON 檔案，若不存在則回傳預設值。"""
    if not path.exists():
        return default
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return default
    return json.loads(content)


def save_json_atomic(path: Path, data: Any) -> None:
    """以暫存檔原子寫入 UTF-8 JSON，避免中途中斷造成檔案損毀。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def find_existing_product(
    entry: UrlEntry,
    products: list[dict[str, Any]],
    url_map: dict[str, str],
) -> dict[str, Any] | None:
    """比對 products.json 與網址快取表，檢查該網址是否已收錄。"""
    norm_product = normalize_url(entry.product_url)
    norm_affiliate = normalize_url(entry.affiliate_url)
    by_slug = {str(item.get("slug", "")): item for item in products}

    mapped_slug = url_map.get(norm_product) or url_map.get(norm_affiliate)
    if mapped_slug and mapped_slug in by_slug:
        return by_slug[mapped_slug]

    for item in products:
        existing_aff = str(item.get("coupang_affiliate_url", "")).strip()
        if not existing_aff:
            continue
        norm_existing = normalize_url(existing_aff)
        if norm_existing in (norm_product, norm_affiliate) or existing_aff in (
            entry.product_url,
            entry.affiliate_url,
        ):
            return item

    return None


def looks_blocked_or_empty(text: str, status_code: int | None) -> str | None:
    """判斷頁面回應是否為 HTTP 錯誤、反爬蟲挑戰頁或空白頁面。"""
    if status_code is not None and status_code >= 400:
        return f"網站回傳 HTTP 狀態碼 {status_code}"

    lowered = text.lower()
    for indicator in BLOCK_INDICATORS:
        if indicator in lowered:
            return f"偵測到反爬蟲或驗證提示（{indicator}）"

    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) < 40:
        return "頁面文字內容過少，可能尚未完成載入或遭阻擋"

    if not re.search(r"\d", compact):
        return "頁面中找不到任何數字或價格資訊"

    return None


def fetch_page_with_playwright(
    url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 25000,
) -> ScrapeResult:
    """使用 Playwright Chromium 開啟酷澎商品頁並擷取標題、價格與內文摘要。"""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ScrapeResult(
            success=False,
            error=(
                "尚未安裝 playwright 套件，請先執行 "
                "`pip install -r scripts/requirements.txt && playwright install chromium`"
            ),
        )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                locale="zh-TW",
                timezone_id="Asia/Taipei",
                user_agent=DESKTOP_USER_AGENT,
                viewport={"width": 1366, "height": 900},
                extra_http_headers={
                    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            status_code = response.status if response else None

            selector_candidates = (
                "h1, h2.prod-buy-header__title, .prod-price, [class*='price'], [class*='Price']"
            )
            try:
                page.wait_for_selector(selector_candidates, timeout=8000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(2000)

            page_title = page.title().strip()
            extracted = page.evaluate(
                """() => {
                    const pickText = (selector, limit = 8) =>
                        Array.from(document.querySelectorAll(selector))
                            .map((el) => (el.innerText || '').trim())
                            .filter(Boolean)
                            .slice(0, limit);
                    const headings = pickText('h1, h2, [class*="title"], [class*="Title"]', 6);
                    const prices = pickText('.prod-price, [class*="price"], [class*="Price"]', 10);
                    const bodyText = (document.body ? document.body.innerText : '').trim();
                    return { headings, prices, bodyText };
                }"""
            )
            context.close()
            browser.close()
    except PlaywrightTimeoutError:
        return ScrapeResult(success=False, error=f"網頁載入超過 {timeout_ms // 1000} 秒逾時")
    except Exception as exc:  # noqa: BLE001
        return ScrapeResult(success=False, error=f"Playwright 執行失敗：{exc}")

    body_text = str(extracted.get("bodyText", ""))
    block_reason = looks_blocked_or_empty(f"{page_title}\n{body_text}", status_code)
    if block_reason:
        return ScrapeResult(success=False, error=block_reason)

    headings = "\n".join(extracted.get("headings", []))
    prices = "\n".join(extracted.get("prices", []))
    trimmed_body = re.sub(r"\n{3,}", "\n\n", body_text)[:4500]

    combined = (
        f"【網頁 Title】\n{page_title}\n\n"
        f"【標題候選】\n{headings}\n\n"
        f"【價格區段】\n{prices}\n\n"
        f"【頁面內文摘錄】\n{trimmed_body}"
    )
    return ScrapeResult(success=True, raw_text=combined)


def prompt_manual_paste(url: str, reason: str) -> str | None:
    """爬蟲受阻或逾時時的終端機手動貼上備援輸入。"""
    print("\n" + "-" * 60)
    print(f"⚠️  無法自動擷取商品頁：{url}")
    print(f"    原因：{reason}")
    print("💡 備援模式：請直接在瀏覽器開啟該商品，複製「商品標題與價格文字」貼到下方。")
    print("   貼上後連續按兩次 Enter（或輸入單獨一行 END）送出；直接按 Enter 或輸入 skip 可略過。")
    print("-" * 60)

    lines: list[str] = []
    empty_streak = 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        stripped = line.strip()
        if not lines and stripped.lower() in ("", "skip"):
            return None
        if stripped.upper() == "END":
            break
        if not stripped:
            empty_streak += 1
            if empty_streak >= 2:
                break
            lines.append("")
            continue
        empty_streak = 0
        lines.append(line)

    text = "\n".join(lines).strip()
    return text or None


def sanitize_slug(raw_slug: str, fallback_seed: str) -> str:
    """確保 slug 為乾淨的全小寫英文數字與連字號格式。"""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", raw_slug.strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if cleaned:
        return cleaned
    digest = hashlib.sha1(fallback_seed.encode("utf-8")).hexdigest()[:8]
    return f"daily-item-{digest}"


def normalize_number(value: float) -> int | float:
    """若浮點數實質上為整數則轉成 int，否則保留適當小數位。"""
    rounded_int = round(value)
    if abs(value - rounded_int) < 1e-6:
        return int(rounded_int)
    return round(value, 2)


def _strip_duplicate_brand(brand: str, name: str) -> str:
    """移除商品名稱開頭重複出現的品牌前綴（避免前端顯示『倍潔雅 倍潔雅 ...』）。"""
    cleaned_brand = brand.strip()
    cleaned_name = name.strip()
    if not cleaned_brand or not cleaned_name:
        return cleaned_name

    candidates = [cleaned_brand, *cleaned_brand.split()]
    for token in candidates:
        if len(token) < 2:
            continue
        pattern = re.compile(rf"^{re.escape(token)}[\s\-_/·]+", re.IGNORECASE)
        if pattern.match(cleaned_name):
            stripped = pattern.sub("", cleaned_name, count=1).strip()
            if stripped:
                return stripped
    return cleaned_name


def normalize_parsed_product(
    parsed: ParsedProduct,
    entry: UrlEntry,
    today_str: str,
) -> dict[str, Any]:
    """校正數值一致性（例如 total_units = pack_count * units_per_pack）並補齊中繼欄位。"""
    clean_brand = parsed.brand.strip()
    clean_name = _strip_duplicate_brand(clean_brand, parsed.name)
    slug = sanitize_slug(parsed.slug, f"{clean_brand}-{clean_name}-{entry.product_url}")
    pack_count = max(1, int(parsed.pack_count))
    units_per_pack = normalize_number(float(parsed.units_per_pack))
    total_units = normalize_number(pack_count * float(units_per_pack))

    coupang_price = max(1, int(round(parsed.coupang_price)))
    regular_price = int(round(parsed.regular_price))
    if regular_price <= coupang_price:
        regular_price = max(coupang_price + 1, int(round(coupang_price * 1.25)))
        print(
            f"ℹ️  [{slug}] AI 回傳原價低於或等於特價，已自動調整 regular_price -> NT${regular_price}"
        )

    god_tier_unit_price = round(max(0.0001, float(parsed.god_tier_unit_price)), 4)

    features = [feat.strip() for feat in parsed.features if feat.strip()][:3]
    while len(features) < 3:
        features.append("宅配整箱送到家，不用自己從賣場扛上樓")

    editor_note = (getattr(parsed, "editor_note", "") or "").strip()
    if not editor_note:
        unit_val = coupang_price / float(total_units)
        editor_note = (
            f"目前算下來每{parsed.unit_name.strip()}約 {unit_val:.3f} 元，"
            "下單前建議再對一下賣場選單裡的實際包數與容量。"
        )

    return {
        "slug": slug,
        "category": parsed.category,
        "brand": clean_brand,
        "name": clean_name,
        "unit_name": parsed.unit_name.strip(),
        "pack_count": pack_count,
        "units_per_pack": units_per_pack,
        "total_units": total_units,
        "regular_price": regular_price,
        "coupang_price": coupang_price,
        "god_tier_unit_price": god_tier_unit_price,
        "coupang_affiliate_url": entry.affiliate_url,
        "features": features,
        "best_for": parsed.best_for.strip(),
        "editor_note": editor_note,
        "updated_at": today_str,
    }


DEFAULT_GEMINI_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-2.5-flash",
)


def resolve_gemini_model_candidates(explicit_model: str | None = None) -> list[str]:
    """依優先序回傳可用的 Gemini 模型候選清單（支援 --model 與 .env 的 GEMINI_MODEL）。"""
    ordered: list[str] = []
    for candidate in (explicit_model, os.getenv("GEMINI_MODEL"), *DEFAULT_GEMINI_MODELS):
        cleaned = (candidate or "").strip()
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def generate_content_with_fallback(
    client: genai.Client,
    *,
    contents: Any,
    config: types.GenerateContentConfig,
    model_name: str | None = None,
) -> tuple[Any, str]:
    """呼叫 Gemini generate_content，遇 404/429/5xx 模型不可用時自動切換備援模型。"""
    candidates = resolve_gemini_model_candidates(model_name)
    last_exc: Exception | None = None
    for idx, candidate in enumerate(candidates):
        try:
            response = client.models.generate_content(
                model=candidate,
                contents=contents,
                config=config,
            )
            return response, candidate
        except genai_errors.APIError as exc:
            if exc.code in (404, 429, 500, 503, 504) and idx < len(candidates) - 1:
                print(
                    f"ℹ️  模型 {candidate} 暫時無法使用（HTTP {exc.code}），改試 {candidates[idx + 1]}..."
                )
                last_exc = exc
                continue
            raise
    assert last_exc is not None
    raise last_exc


def parse_product_with_gemini(
    raw_text: str,
    entry: UrlEntry,
    client: genai.Client,
    today_str: str,
    *,
    model_name: str | None = None,
    return_used_model: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], str]:
    """呼叫 Gemini Structured Outputs 將商品文字拆解為標準 JSON 規格。"""
    system_instruction = (
        "你是台灣 PTT Lifeismoney 省錢版與租屋社群出身的日用品比價筆記作者。"
        "請從提供的商品頁面文字或標題價格摘錄中，精確拆解箱購規格與售價，並寫出像真人的買前筆記。"
        "務必遵守：\n"
        "1. 全部文字欄位使用台灣繁體中文口語，禁止出現「CP值高、高性價比、超實用、必買、激推、品質安心有保障、滿足各種需求、打造舒適生活」等 AI 廣告腔調。\n"
        "2. slug 僅能使用小寫英文字母、數字與連字號（-），須包含品牌、關鍵型號與規格數字。\n"
        "3. name 欄位不可重複包含 brand 品牌名稱（例如 brand 填「倍潔雅」時，name 直接寫「抽取式衛生紙 (150抽×60包)」）。\n"
        "4. category 只能是「衛生紙、洗衣膠囊、濕紙巾、紙尿褲、清潔劑、其他日用品」其中之一。\n"
        "5. total_units 必須精確等於 pack_count * units_per_pack。\n"
        "6. regular_price 必須是合理高於 coupang_price 的一般通路原價。\n"
        "7. god_tier_unit_price 請依據台灣省錢社群對該類商品的歷史認知，推估合理且有參考價值的「神價門檻單價」（小數點後 3~4 位）。\n"
        "8. features 與 editor_note 要寫實際買家會在意的細節或小缺點（例如：紙箱太大很佔位、無蓋款需裝濕巾盒、單包買單片較貴、大瓶很重不要自己扛等）；若原標題未寫明完整包數，請依售價合理推估並在 editor_note 提醒點進賣場確認選項包數。"
    )

    user_prompt = (
        f"商品網址：{entry.product_url}\n"
        f"請分析以下擷取到的商品資訊並輸出結構化 JSON：\n\n{raw_text}"
    )

    response, used_model = generate_content_with_fallback(
        client,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=ParsedProduct,
            temperature=0.25,
        ),
        model_name=model_name,
    )

    parsed: ParsedProduct | None = getattr(response, "parsed", None)
    if parsed is None:
        if not response.text:
            raise ValueError("Gemini 未回傳任何 JSON 內容")
        parsed = ParsedProduct.model_validate_json(response.text)

    normalized = normalize_parsed_product(parsed, entry, today_str)
    if return_used_model:
        return normalized, used_model
    return normalized


def upsert_product(
    products: list[dict[str, Any]],
    new_item: dict[str, Any],
    matched_slug: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """依 slug 更新既有商品或新增商品；若已知原本匹配的 slug 則一併覆寫避免重複。"""
    target_slugs = {new_item["slug"]}
    if matched_slug:
        target_slugs.add(matched_slug)

    updated = list(products)
    for idx, existing in enumerate(updated):
        if existing.get("slug") in target_slugs:
            # 保留原本 slug 避免現有 SEO 連結失效
            preserved_slug = existing.get("slug") or new_item["slug"]
            updated[idx] = {**new_item, "slug": preserved_slug}
            return updated, "updated"

    updated.append(new_item)
    return updated, "added"


def sync_and_deploy(
    project_root: Path = PROJECT_ROOT,
    *,
    run_build: bool = True,
    auto_git: bool = False,
    ask_git: bool = False,
    commit_message: str | None = None,
) -> int:
    """驗證 Astro 建置（npm run build），並可選擇執行 git add / commit / push。"""
    if run_build:
        npm_bin = shutil.which("npm")
        if not npm_bin:
            print("❌ 找不到 npm 指令，無法執行 `npm run build`。請確認已安裝 Node.js 22+。")
            return 1
        print("\n🔨 正在執行 `npm run build` 驗證網站與 sitemap 建置...")
        build_proc = subprocess.run([npm_bin, "run", "build"], cwd=project_root, check=False)
        if build_proc.returncode != 0:
            print("❌ `npm run build` 建置失敗，已中止 Git 推送流程。")
            return build_proc.returncode
        print("✅ Astro 靜態建置驗證通過！")

    should_git = auto_git
    if not should_git and ask_git:
        answer = input("\n是否自動執行 git add / commit / push 部署上線？[y/N]: ").strip().lower()
        should_git = answer in ("y", "yes")

    if not should_git:
        return 0

    git_bin = shutil.which("git")
    if not git_bin:
        print("❌ 找不到 git 指令，無法自動提交與推送。")
        return 1

    status_proc = subprocess.run(
        [git_bin, "status", "--porcelain", "--", "src/data/products.json", "scripts/urls.txt"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status_proc.returncode != 0:
        print(f"❌ 無法讀取 Git 狀態（請確認是否已 git init）：{status_proc.stderr.strip()}")
        return 1
    if not status_proc.stdout.strip():
        print("ℹ️  src/data/products.json 與 scripts/urls.txt 沒有變更，無需 commit / push。")
        return 0

    today_str = dt.date.today().isoformat()
    msg = commit_message or f"chore(data): update daily product prices ({today_str})"

    for cmd in (
        [git_bin, "add", "src/data/products.json", "scripts/urls.txt"],
        [git_bin, "commit", "-m", msg],
        [git_bin, "push"],
    ):
        print(f"🚀 執行：{' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=project_root, check=False)
        if proc.returncode != 0:
            print(f"❌ 指令失敗：{' '.join(cmd)}")
            return proc.returncode

    print("🎉 已成功推送至遠端倉庫！")
    return 0


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_env_configuration(
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, str], str]:
    """依優先序載入 .env（daily-price-calc/.env > Coupang_Affiliate/.env > 系統環境變數）。"""
    sibling_env = project_root.parent / "Coupang_Affiliate" / ".env"
    local_env = project_root / ".env"

    merged_dotenv: dict[str, str] = {}
    key_source = "系統或終端機環境變數"
    for env_path in (sibling_env, local_env):
        if not env_path.is_file():
            continue
        for key, raw_val in dotenv_values(env_path).items():
            if raw_val is None:
                continue
            cleaned = raw_val.strip()
            if not cleaned:
                continue
            merged_dotenv[key] = cleaned
            os.environ[key] = cleaned
            if key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                key_source = str(env_path)

    return merged_dotenv, key_source


def build_genai_client(
    api_key: str,
    *,
    dotenv_vars: dict[str, str] | None = None,
) -> genai.Client:
    """建立 Google GenAI Client，預設明確使用 Gemini Developer API（v1beta）並關閉慢速重試以即時切換備援模型。"""
    env_source = dotenv_vars or {}
    explicit_enterprise = _is_truthy(env_source.get("GOOGLE_GENAI_USE_ENTERPRISE")) or _is_truthy(
        env_source.get("GOOGLE_GENAI_USE_VERTEXAI")
    )
    no_sdk_retry = types.HttpRetryOptions(attempts=1)
    if explicit_enterprise:
        return genai.Client(
            enterprise=True,
            api_key=api_key,
            http_options=types.HttpOptions(retry_options=no_sdk_retry),
        )
    return genai.Client(
        enterprise=False,
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta", retry_options=no_sdk_retry),
    )


def verify_gemini_connection(
    client: genai.Client,
    *,
    model_name: str | None = None,
) -> tuple[str, str]:
    """發送最小請求驗證 Gemini 模型連線正常，回傳 (回覆文字, 實際使用模型)。"""
    response, used_model = generate_content_with_fallback(
        client,
        contents="請只回覆 OK",
        config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=256),
        model_name=model_name,
    )
    return (response.text or "").strip(), used_model


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="從 scripts/urls.txt 批次擷取酷澎商品並以 Gemini 寫入 src/data/products.json"
    )
    parser.add_argument(
        "--urls",
        type=Path,
        default=DEFAULT_URLS_FILE,
        help="商品網址清單路徑（預設：scripts/urls.txt）",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="直接從 Coupang_Affiliate 匯出的 JSON 檔匯入商品（免 Playwright 爬蟲）",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="改用有頭瀏覽器（headless=False）執行 Playwright，方便觀察頁面載入",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="跳過 Playwright 抓取，直接以終端機貼上文字進行 AI 解析",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使該網址今日已更新過，仍強制重新抓取並更新",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="Playwright 載入逾時秒數（預設：25）",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="更新 JSON 後自動執行 `npm run build` 並詢問是否 git push",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="更新 JSON 後自動執行 `npm run build` 並直接執行 git add/commit/push",
    )
    parser.add_argument(
        "--commit-msg",
        type=str,
        default=None,
        help="自訂 Git commit 訊息",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="指定優先使用的 Gemini 模型（預設依序使用 GEMINI_MODEL、gemini-3.8-flash、gemini-3-flash-preview）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="僅檢查 GEMINI_API_KEY 來源與 Gemini 模型連線是否正常（不修改 products.json）",
    )
    return parser


def _configure_stdio_utf8() -> None:
    """確保 Windows 終端機或子程序管線輸出 UTF-8 而不因 emoji 觸發 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> int:
    _configure_stdio_utf8()
    dotenv_vars, key_source = load_env_configuration(PROJECT_ROOT)
    args = build_arg_parser().parse_args()

    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        print("❌ 未設定環境變數 GEMINI_API_KEY。")
        print("   PowerShell 設定方式：$env:GEMINI_API_KEY = '你的_API_KEY'")
        print("   或寫入專案根目錄 .env 檔案：GEMINI_API_KEY=你的_API_KEY")
        return 1

    client = build_genai_client(api_key, dotenv_vars=dotenv_vars)
    endpoint_mode = "Vertex AI / Agent Platform" if client._api_client.vertexai else "Gemini Developer API"
    key_prefix = f"{api_key[:4]}..." if len(api_key) > 4 else "***"
    print(f"🔑 Gemini 設定：{endpoint_mode} | 金鑰來源：{key_source}（前綴 {key_prefix}）")

    if args.check:
        try:
            reply, used_model = verify_gemini_connection(client, model_name=args.model)
            print(f"✅ Gemini 連線正常（模型：{used_model}）！測試回覆：{reply or '(empty)'}")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Gemini 連線失敗：{exc}")
            return 1

    try:
        if args.from_json:
            feed_items = parse_feed_json_file(args.from_json)
            if not feed_items:
                print(f"ℹ️  {args.from_json} 沒有可匯入的商品資料。")
                return 0
        else:
            entries = parse_urls_file(args.urls)
            if not entries:
                print(f"ℹ️  {args.urls} 目前沒有有效網址，請先加入酷澎商品網址後再執行。")
                return 0
            feed_items = [FeedItem(entry=e, raw_text="") for e in entries]
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"❌ {exc}")
        return 1

    today_str = dt.date.today().isoformat()

    products: list[dict[str, Any]] = load_json_file(PRODUCTS_JSON_PATH, [])
    url_map: dict[str, str] = load_json_file(URL_MAP_PATH, {})

    processed_count = 0
    skipped_count = 0
    failed_count = 0
    active_model: str | None = args.model

    for idx, feed_item in enumerate(feed_items, start=1):
        entry = feed_item.entry
        print(f"\n[{idx}/{len(feed_items)}] 處理中：{entry.product_url}")
        existing = find_existing_product(entry, products, url_map)
        if existing is None and feed_item.product_id:
            mapped_by_id = url_map.get(f"pid:{feed_item.product_id}")
            if mapped_by_id:
                existing = next((p for p in products if p.get("slug") == mapped_by_id), None)
        matched_slug = str(existing["slug"]) if existing else None

        if existing and existing.get("updated_at") == today_str and not args.force:
            print(f"⏭️  已收錄且今日（{today_str}）已更新過，略過：{matched_slug}")
            skipped_count += 1
            continue

        raw_text: str | None = feed_item.raw_text or None
        if not raw_text:
            if args.manual:
                raw_text = prompt_manual_paste(entry.product_url, "已啟用 --manual 手動輸入模式")
            else:
                scrape = fetch_page_with_playwright(
                    entry.product_url,
                    headless=not args.headed,
                    timeout_ms=args.timeout * 1000,
                )
                if scrape.success:
                    raw_text = scrape.raw_text
                    print("🌐 Playwright 成功擷取頁面文字。")
                else:
                    raw_text = prompt_manual_paste(entry.product_url, scrape.error)

        if not raw_text:
            print("⏭️  已略過此商品。")
            skipped_count += 1
            continue

        try:
            target_label = active_model or resolve_gemini_model_candidates()[0]
            print(f"🤖 正在透過 Gemini ({target_label}) 拆解規格與換算神價門檻...")
            parsed_result = parse_product_with_gemini(
                raw_text,
                entry,
                client,
                today_str,
                model_name=active_model,
                return_used_model=True,
            )
            parsed_item, used_model = parsed_result  # type: ignore[misc]
            active_model = used_model
            if feed_item.coupang_price and parsed_item["coupang_price"] != feed_item.coupang_price:
                parsed_item["coupang_price"] = feed_item.coupang_price
                if parsed_item["regular_price"] <= parsed_item["coupang_price"]:
                    parsed_item["regular_price"] = max(
                        parsed_item["coupang_price"] + 1,
                        int(round(parsed_item["coupang_price"] * 1.25)),
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Gemini 解析失敗：{exc}")
            failed_count += 1
            continue

        products, action = upsert_product(products, parsed_item, matched_slug=matched_slug)
        final_slug = parsed_item["slug"] if action == "added" else (matched_slug or parsed_item["slug"])

        url_map[normalize_url(entry.product_url)] = final_slug
        url_map[normalize_url(entry.affiliate_url)] = final_slug
        if feed_item.product_id:
            url_map[f"pid:{feed_item.product_id}"] = final_slug

        save_json_atomic(PRODUCTS_JSON_PATH, products)
        save_json_atomic(URL_MAP_PATH, url_map)

        unit_price = parsed_item["coupang_price"] / parsed_item["total_units"]
        action_label = "更新" if action == "updated" else "新增"
        print(
            f"✅ 已{action_label} [{final_slug}] {parsed_item['brand']} {parsed_item['name']} | "
            f"NT${parsed_item['coupang_price']} / {parsed_item['total_units']}{parsed_item['unit_name']} "
            f"(單價 {unit_price:.4f})"
        )
        processed_count += 1

    print(
        f"\n📊 本次完成：成功寫入 {processed_count} 筆，略過 {skipped_count} 筆，失敗 {failed_count} 筆。"
        f"目前資料庫共 {len(products)} 筆商品。"
    )

    if args.deploy or args.sync:
        return sync_and_deploy(
            PROJECT_ROOT,
            run_build=True,
            auto_git=args.deploy,
            ask_git=args.sync and not args.deploy,
            commit_message=args.commit_msg,
        )

    return 1 if failed_count > 0 and processed_count == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
