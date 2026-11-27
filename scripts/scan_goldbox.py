#!/usr/bin/env python3
"""Scan Coupang Taiwan Goldbox deals for uncollected household/daily consumables."""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import scripts.update_prices as u  # noqa: E402

HOUSEHOLD_KEYWORDS = (
    "衛生紙",
    "紙巾",
    "濕巾",
    "濕紙巾",
    "洗衣",
    "洗碗",
    "洗潔",
    "清潔",
    "保鮮膜",
    "密實袋",
    "耐熱袋",
    "菜瓜布",
    "抹布",
    "濾芯",
    "濾網",
    "紙尿褲",
    "尿布",
    "暖暖包",
    "垃圾袋",
    "清潔袋",
    "貓砂",
    "掃把",
    "紙杯",
)


def main() -> int:
    u._configure_stdio_utf8()
    ak, sk, dom = u.load_coupang_credentials(PROJECT_ROOT)
    if not ak or not sk:
        print("❌ 缺少 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY", file=sys.stderr)
        return 1

    url_map_path = PROJECT_ROOT / "scripts" / ".url_map.json"
    existing_pids: set[str] = set()
    if url_map_path.is_file():
        url_map = json.loads(url_map_path.read_text(encoding="utf-8"))
        existing_pids = {k[4:] for k in url_map if k.startswith("pid:")}

    payload = u.coupang_get_json(
        dom,
        "/v2/providers/affiliate_open_api/apis/openapi/v1/products/goldbox",
        {"imageSize": "512x512"},
        ak,
        sk,
    )
    rows = payload.get("data", []) if isinstance(payload.get("data"), list) else []

    new_household: list[dict] = []
    other_new: list[dict] = []
    for row in rows:
        pid = str(row.get("productId") or "").strip()
        if not pid or pid in existing_pids:
            continue
        name = str(row.get("productName") or "").strip()
        if any(kw in name for kw in HOUSEHOLD_KEYWORDS):
            new_household.append(row)
        else:
            other_new.append(row)

    print(f"🔎 今日 Coupang Goldbox 總計 {len(rows)} 檔 | 尚未收錄的日用消耗品候選：{len(new_household)} 檔\n")
    for r in new_household:
        print(
            f"  [日用] pid={r.get('productId')} | NT${int(round(float(r.get('productPrice') or 0)))} | "
            f"{r.get('productName')}"
        )

    if other_new:
        print(f"\n📦 其他今日 Goldbox 特賣品（共 {len(other_new)} 檔）：")
        for r in other_new[:10]:
            print(
                f"  [其他] pid={r.get('productId')} | NT${int(round(float(r.get('productPrice') or 0)))} | "
                f"{r.get('productName')}"
            )

    print(
        "\n💡 若要把今日 Goldbox 居家特賣新品一次轉短網址並匯入（需已設定 GEMINI_API_KEY）：\n"
        "   python ..\\Coupang_Affiliate\\sync_daily_price_calc.py --mode goldbox --limit 10"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
