#!/usr/bin/env python3
"""Safely refresh Coupang prices in src/data/products.json without overwriting curated specs/copy."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_PATH = PROJECT_ROOT / "src" / "data" / "products.json"
SOURCES_PATH = Path(__file__).resolve().parent / "price_sources.json"
TAIPEI_TZ = timezone(timedelta(hours=8))
DEFAULT_DOMAIN = "https://api-gateway.tw.coupang.com"


@dataclass(frozen=True)
class CoupangCandidate:
    product_id: str
    product_name: str
    price: int
    product_image: str = ""


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def parse_env_file(env_path: Path) -> dict[str, str]:
    """Minimal .env parser using only stdlib so CI requires zero pip packages."""
    if not env_path.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if value:
            parsed[key] = value
    return parsed


def load_coupang_credentials(project_root: Path) -> tuple[str, str, str]:
    """Load COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY from env or local .env files."""
    merged: dict[str, str] = {}
    for candidate in (
        project_root.parent / "Coupang_Affiliate" / ".env",
        project_root / ".env",
    ):
        merged.update(parse_env_file(candidate))

    access_key = (os.environ.get("COUPANG_ACCESS_KEY") or merged.get("COUPANG_ACCESS_KEY", "")).strip()
    secret_key = (os.environ.get("COUPANG_SECRET_KEY") or merged.get("COUPANG_SECRET_KEY", "")).strip()
    domain = (
        os.environ.get("COUPANG_API_DOMAIN")
        or merged.get("COUPANG_API_DOMAIN")
        or DEFAULT_DOMAIN
    ).strip().rstrip("/")
    return access_key, secret_key, domain


def sign_coupang_request(method: str, path_and_query: str, access_key: str, secret_key: str) -> str:
    if "?" in path_and_query:
        path, query = path_and_query.split("?", 1)
    else:
        path, query = path_and_query, ""
    timestamp = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    message = f"{timestamp}{method.upper()}{path}{query}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        f"CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={timestamp}, "
        f"signature={signature}"
    )


def coupang_get_json(
    domain: str,
    path: str,
    query_params: dict[str, Any],
    access_key: str,
    secret_key: str,
) -> dict[str, Any]:
    filtered = {k: v for k, v in query_params.items() if v is not None and v != ""}
    path_and_query = f"{path}?{urllib.parse.urlencode(filtered)}" if filtered else path
    headers = {
        "Authorization": sign_coupang_request("GET", path_and_query, access_key, secret_key),
        "Content-Type": "application/json;charset=UTF-8",
        "X-MARKET": "TW",
    }
    req = urllib.request.Request(f"{domain}{path_and_query}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Coupang API HTTP {exc.code} at {path}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error calling Coupang API {path}: {exc}") from exc

    if str(payload.get("rCode", "0")) != "0":
        raise RuntimeError(
            f"Coupang API rCode={payload.get('rCode')} at {path}: {payload.get('rMessage', '')}"
        )
    return payload


def extract_candidates(payload: dict[str, Any]) -> list[CoupangCandidate]:
    data = payload.get("data")
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        for key in ("result", "productData", "products", "items"):
            nested = data.get(key)
            if isinstance(nested, list):
                rows = [r for r in nested if isinstance(r, dict)]
                break

    out: list[CoupangCandidate] = []
    for raw in rows:
        pid = str(raw.get("productId") or "").strip()
        name = str(raw.get("productName") or "").strip()
        raw_img = str(raw.get("productImage") or "").strip()
        if raw_img.startswith("//"):
            raw_img = f"https:{raw_img}"
        raw_price = raw.get("productPrice")
        try:
            price = int(round(float(raw_price)))
        except (TypeError, ValueError):
            continue
        if pid and name and price > 0:
            out.append(
                CoupangCandidate(
                    product_id=pid,
                    product_name=name,
                    price=price,
                    product_image=raw_img,
                )
            )
    return out


def fetch_source_pool(
    source_type: str,
    source_arg: str,
    domain: str,
    access_key: str,
    secret_key: str,
) -> list[CoupangCandidate]:
    if source_type == "bestcategories":
        path = f"/v2/providers/affiliate_open_api/apis/openapi/v1/products/bestcategories/{source_arg}"
        payload = coupang_get_json(domain, path, {"limit": 100, "imageSize": "512x512"}, access_key, secret_key)
        return extract_candidates(payload)
    if source_type == "goldbox":
        path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/goldbox"
        payload = coupang_get_json(domain, path, {"imageSize": "512x512"}, access_key, secret_key)
        return extract_candidates(payload)
    if source_type == "search":
        path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
        payload = coupang_get_json(
            domain,
            path,
            {"keyword": source_arg, "limit": 10, "imageSize": "512x512"},
            access_key,
            secret_key,
        )
        return extract_candidates(payload)
    raise ValueError(f"Unsupported source_type: {source_type}")


def pick_matching_candidate(
    pool: list[CoupangCandidate],
    spec: dict[str, Any],
    current_price: int,
    *,
    allow_deep_promo: bool = False,
) -> CoupangCandidate | None:
    target_pid = str(spec["product_id"]).strip()
    tokens = [str(t).strip().lower() for t in spec.get("required_tokens", []) if str(t).strip()]
    min_price = int(spec.get("min_price", 1))
    if allow_deep_promo and tokens:
        # 大促或限時特賣時只要包數/規格關鍵字完全吻合，放寬最低價下限以捕捉深折活動價
        min_price = max(30, int(min_price * 0.72))
    max_price = int(spec.get("max_price", 999999))

    matches: list[CoupangCandidate] = []
    for cand in pool:
        if cand.product_id != target_pid:
            continue
        lowered_name = cand.product_name.lower()
        if any(tok not in lowered_name for tok in tokens):
            continue
        if not (min_price <= cand.price <= max_price):
            continue
        matches.append(cand)

    if not matches:
        return None

    # If multiple bundles share the title, prefer the one closest to the single-box price already verified.
    matches.sort(key=lambda c: abs(c.price - current_price))
    return matches[0]


def save_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="更新 daily-price-calc 酷澎售價並保留人工校對規格")
    parser.add_argument("--dry-run", action="store_true", help="僅預覽價格差異，不寫入 products.json")
    parser.add_argument(
        "--touch-date",
        action="store_true",
        help="即使價格無變動，也為成功比對到的商品刷新 updated_at 日期",
    )
    parser.add_argument(
        "--refresh-images",
        action="store_true",
        help="一併將所有比對到的商品 image_url 同步成 API 最新回傳的圖片網址",
    )
    parser.add_argument(
        "--promo-label",
        default="",
        help="指定大檔降價時的活動標籤（例如：黑五特價、雙12特價；也可由 COUPANG_PROMO_LABEL 環境變數帶入）",
    )
    return parser


def main() -> int:
    _configure_stdio_utf8()
    args = build_arg_parser().parse_args()
    promo_label_override = (args.promo_label or os.environ.get("COUPANG_PROMO_LABEL", "")).strip()

    access_key, secret_key, domain = load_coupang_credentials(PROJECT_ROOT)
    if not access_key or not secret_key:
        print(
            "❌ 缺少 COUPANG_ACCESS_KEY 或 COUPANG_SECRET_KEY。\n"
            "   若在 GitHub Actions 執行，請到 Repo Settings -> Secrets and variables -> Actions 新增這兩個 Secret。",
            file=sys.stderr,
        )
        return 1

    products: list[dict[str, Any]] = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    sources: list[dict[str, Any]] = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    source_by_slug = {str(item["slug"]): item for item in sources}

    pool_cache: dict[tuple[str, str], list[CoupangCandidate]] = {}

    def get_pool(source_type: str, source_arg: str) -> list[CoupangCandidate]:
        key = (source_type, source_arg)
        if key not in pool_cache:
            try:
                pool_cache[key] = fetch_source_pool(source_type, source_arg, domain, access_key, secret_key)
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️  查詢 {source_type}:{source_arg} 失敗：{exc}")
                pool_cache[key] = []
        return pool_cache[key]

    today_date = datetime.now(TAIPEI_TZ).date()
    today_str = today_date.isoformat()
    promo_3d_str = (today_date + timedelta(days=3)).isoformat()
    promo_1d_str = (today_date + timedelta(days=1)).isoformat()
    goldbox_pool = get_pool("goldbox", "goldbox")

    changed_count = 0
    matched_count = 0
    skipped_count = 0

    for item in products:
        slug = str(item.get("slug", ""))
        spec = source_by_slug.get(slug)
        if not spec:
            skipped_count += 1
            continue

        cur_price = int(item.get("coupang_price", 0))
        goldbox_match = pick_matching_candidate(goldbox_pool, spec, cur_price, allow_deep_promo=True)

        source_match: CoupangCandidate | None = None
        if str(spec["source_type"]) != "goldbox":
            pool = get_pool(str(spec["source_type"]), str(spec["source_arg"]))
            has_tokens = bool([t for t in spec.get("required_tokens", []) if str(t).strip()])
            source_match = pick_matching_candidate(pool, spec, cur_price, allow_deep_promo=has_tokens)
        elif goldbox_match is None:
            # Goldbox rotates daily; if item rotated off goldbox, try fallback search safely.
            fallback_pool = get_pool("search", "廚房濕巾 80抽")
            source_match = pick_matching_candidate(fallback_pool, spec, cur_price, allow_deep_promo=True)

        in_goldbox = False
        if goldbox_match is not None and (source_match is None or goldbox_match.price <= source_match.price):
            match = goldbox_match
            in_goldbox = True
        else:
            match = source_match

        if match is None:
            print(f"⏭️  [{slug}] 暫無符合規格與安全價位區間的回傳結果，維持 NT${cur_price}")
            skipped_count += 1
            continue

        matched_count += 1
        item_updated = False
        cur_image = str(item.get("image_url") or "").strip()

        if match.price != cur_price:
            dropped = match.price < cur_price
            drop_pct = ((cur_price - match.price) / cur_price) if cur_price > 0 else 0.0
            print(f"🔄 [{slug}] NT${cur_price} -> NT${match.price} ({match.product_name})")
            if dropped and (drop_pct >= 0.03 or (cur_price - match.price) >= 15 or in_goldbox):
                item["previous_price"] = cur_price
                item["promo_tag"] = promo_label_override or ("Goldbox 限時特賣" if in_goldbox else "限時降價")
                item["promo_until"] = promo_1d_str if in_goldbox else promo_3d_str
            elif not dropped:
                item.pop("previous_price", None)
                item.pop("promo_tag", None)
                item.pop("promo_until", None)

            item["coupang_price"] = match.price
            if int(item.get("regular_price", 0)) <= match.price:
                item["regular_price"] = int(round(match.price * 1.2))
            if match.product_image:
                item["image_url"] = match.product_image
            item["updated_at"] = today_str
            item_updated = True
        else:
            expected_gold_tag = promo_label_override or "Goldbox 限時特賣"
            if in_goldbox and item.get("promo_tag") != expected_gold_tag:
                item["promo_tag"] = expected_gold_tag
                item["promo_until"] = promo_1d_str
                print(f"🔥 [{slug}] 標記今日 Goldbox 限時特賣 (NT${cur_price})")
                item_updated = True
            elif not in_goldbox and item.get("promo_until") and str(item["promo_until"]) < today_str:
                item.pop("previous_price", None)
                item.pop("promo_tag", None)
                item.pop("promo_until", None)
                print(f"🧹 [{slug}] 清除已逾期活動標籤")
                item_updated = True

            if not cur_image and match.product_image:
                print(f"🖼️  [{slug}] 補上商品圖片網址")
                item["image_url"] = match.product_image
                item_updated = True
            elif args.refresh_images and match.product_image and match.product_image != cur_image:
                print(f"🖼️  [{slug}] 同步更新商品圖片網址")
                item["image_url"] = match.product_image
                item_updated = True
            if args.touch_date and item.get("updated_at") != today_str:
                item["updated_at"] = today_str
                item_updated = True
            if not item_updated:
                print(f"✅ [{slug}] 價格維持 NT${cur_price}")

        if item_updated:
            changed_count += 1

    print(
        f"\n📊 查價完成：成功核對 {matched_count}/{len(products)} 筆 | "
        f"價格更新 {changed_count} 筆 | 未匹配/略過 {skipped_count} 筆"
    )

    if changed_count > 0 and not args.dry_run:
        save_json_atomic(PRODUCTS_PATH, products)
        print(f"💾 已更新 {PRODUCTS_PATH}")
    elif args.dry_run:
        print("ℹ️  [DRY-RUN] 未寫入 products.json。")

    if matched_count == 0 and len(sources) > 0:
        print("❌ 沒有任何商品成功完成 API 核對，請檢查酷澎 API 金鑰或網路連線。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
