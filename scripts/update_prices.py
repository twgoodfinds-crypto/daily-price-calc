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
        raw_price = raw.get("productPrice")
        try:
            price = int(round(float(raw_price)))
        except (TypeError, ValueError):
            continue
        if pid and name and price > 0:
            out.append(CoupangCandidate(product_id=pid, product_name=name, price=price))
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
        payload = coupang_get_json(domain, path, {"limit": 100}, access_key, secret_key)
        return extract_candidates(payload)
    if source_type == "goldbox":
        path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/goldbox"
        payload = coupang_get_json(domain, path, {}, access_key, secret_key)
        return extract_candidates(payload)
    if source_type == "search":
        path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
        payload = coupang_get_json(domain, path, {"keyword": source_arg, "limit": 10}, access_key, secret_key)
        return extract_candidates(payload)
    raise ValueError(f"Unsupported source_type: {source_type}")


def pick_matching_candidate(
    pool: list[CoupangCandidate],
    spec: dict[str, Any],
    current_price: int,
) -> CoupangCandidate | None:
    target_pid = str(spec["product_id"]).strip()
    tokens = [str(t).strip().lower() for t in spec.get("required_tokens", []) if str(t).strip()]
    min_price = int(spec.get("min_price", 1))
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
    return parser


def main() -> int:
    _configure_stdio_utf8()
    args = build_arg_parser().parse_args()

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

    today_str = datetime.now(TAIPEI_TZ).date().isoformat()
    changed_count = 0
    matched_count = 0
    skipped_count = 0

    for item in products:
        slug = str(item.get("slug", ""))
        spec = source_by_slug.get(slug)
        if not spec:
            skipped_count += 1
            continue

        pool = get_pool(str(spec["source_type"]), str(spec["source_arg"]))
        cur_price = int(item.get("coupang_price", 0))
        match = pick_matching_candidate(pool, spec, cur_price)

        # Goldbox rotates daily; if item rotated off goldbox, try fallback search safely.
        if match is None and spec["source_type"] == "goldbox":
            fallback_pool = get_pool("search", "廚房濕巾 80抽")
            match = pick_matching_candidate(fallback_pool, spec, cur_price)

        if match is None:
            print(f"⏭️  [{slug}] 暫無符合規格與安全價位區間的回傳結果，維持 NT${cur_price}")
            skipped_count += 1
            continue

        matched_count += 1
        if match.price != cur_price:
            print(f"🔄 [{slug}] NT${cur_price} -> NT${match.price} ({match.product_name})")
            item["coupang_price"] = match.price
            if int(item.get("regular_price", 0)) <= match.price:
                item["regular_price"] = int(round(match.price * 1.2))
            item["updated_at"] = today_str
            changed_count += 1
        elif args.touch_date and item.get("updated_at") != today_str:
            item["updated_at"] = today_str
            changed_count += 1
        else:
            print(f"✅ [{slug}] 價格維持 NT${cur_price}")

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
