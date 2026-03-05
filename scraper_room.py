"""
591 租屋監控爬蟲 — 頂溪捷運站附近套房/雅房
- 新北永和區，近捷運
- 套房 + 雅房、≤10000
- 有電梯優先，無電梯限3樓以下
- Telegram Bot 通知
- GitHub Actions 定時執行
"""

import os
import json
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, BrowserContext, Page

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SEEN_FILE = Path(__file__).parent / "seen_ids.json"
PENDING_FILE = Path(__file__).parent / "pending_listings_room.json"

# ── 搜尋設定 ─────────────────────────────────────────────
# 新北市 region=3, 永和區 section=37
SEARCH_CONFIGS = [
    {
        "label": "新北永和（頂溪站）",
        "region": 3,
        "section": "37",
    },
]

COMMON_PARAMS = {
    "kind": "2,3,4",           # 獨立套房、分租套房、雅房
    "rentprice": "0,10000",    # 月租 ≤ 10,000
    "other": "not_cover,near_subway",  # 非頂加、近捷運
    "option": "cold",          # 有冷氣
    "order": "posttime",
    "orderType": "desc",
}

BASE_URL = "https://rent.591.com.tw/list"

EXTRACT_NUXT_JS = """() => {
    const d = window.__NUXT__ && window.__NUXT__.data;
    if (!d) return null;
    for (const v of Object.values(d)) {
        const inner = v && v.data;
        if (inner && inner.items && Array.isArray(inner.items)) {
            return {
                items: inner.items,
                total: inner.total,
                firstRow: inner.firstRow,
            };
        }
    }
    return null;
}"""


# ── Playwright 搜尋 ─────────────────────────────────────
def fetch_listings_pw(context: BrowserContext, config: dict) -> list[dict]:
    params = {**COMMON_PARAMS}
    params["region"] = str(config["region"])
    params["section"] = config["section"]

    all_items: list[dict] = []
    max_pages = 3

    page: Page = context.new_page()

    try:
        for page_num in range(max_pages):
            first_row = page_num * 30
            if first_row > 0:
                params["firstRow"] = str(first_row)
            elif "firstRow" in params:
                del params["firstRow"]

            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{BASE_URL}?{query}"

            logger.info(
                "搜尋 %s | page=%d (firstRow=%d)",
                config["label"], page_num + 1, first_row,
            )

            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                logger.error("頁面載入失敗: %s", e)
                break

            data = page.evaluate(EXTRACT_NUXT_JS)

            if not data or not data.get("items"):
                logger.info("第 %d 頁無資料，結束", page_num + 1)
                break

            items = data["items"]
            total = int(data.get("total", 0)) if data.get("total") else 0
            all_items.extend(items)
            logger.info(
                "取得 %d 筆 (累計 %d / %d)",
                len(items), len(all_items), total,
            )

            if total > 0 and len(all_items) >= total:
                break

            time.sleep(random.uniform(2.0, 4.0))
    finally:
        page.close()

    return all_items


# ── 解析 ─────────────────────────────────────────────────
def _parse_floor(floor_name: str) -> int:
    if not floor_name:
        return 0
    part = floor_name.split("/")[0].strip().upper()
    if part.startswith("B"):
        return 0
    digits = "".join(c for c in part if c.isdigit())
    return int(digits) if digits else 0


def parse_listing(item: dict) -> dict:
    listing_id = str(item.get("id", ""))
    price = item.get("price", "")
    if isinstance(price, str):
        price = price.replace(",", "")
        price = int(price) if price.isdigit() else 0

    tags = item.get("tags", [])
    floor_name = item.get("floor_name", "")

    area_num = item.get("area", 0)
    if isinstance(area_num, str):
        area_num = float(area_num) if area_num.replace(".", "").isdigit() else 0

    return {
        "id": listing_id,
        "title": item.get("title", ""),
        "price": price,
        "address": item.get("address", ""),
        "area": item.get("area_name", item.get("area", "")),
        "area_num": float(area_num),
        "floor": floor_name,
        "floor_num": _parse_floor(floor_name),
        "kind_name": item.get("kind_name", ""),
        "room": item.get("layoutStr", ""),
        "has_elevator": "有電梯" in tags,
        "url": item.get("url", f"https://rent.591.com.tw/{listing_id}"),
        "photo": item.get("cover", ""),
        "refresh_time": item.get("refresh_time", ""),
    }


# ── Seen IDs 管理 ────────────────────────────────────────
def load_seen_ids() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            return set(data)
        except Exception:
            pass
    return set()


def save_seen_ids(ids: set):
    recent = sorted(ids, key=lambda x: int(x) if x.isdigit() else 0)[-5000:]
    SEEN_FILE.write_text(
        json.dumps(recent, ensure_ascii=False),
        encoding="utf-8",
    )


def load_pending_listings() -> list[dict]:
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_pending_listings(listings: list[dict]):
    PENDING_FILE.write_text(
        json.dumps(listings, ensure_ascii=False),
        encoding="utf-8",
    )


def sort_listings(listings: list[dict]) -> list[dict]:
    return sorted(
        listings,
        key=lambda l: (
            int(l["id"]) if l["id"].isdigit() else 0,
            l.get("area_num", 0),
            -l.get("price", 0),
        ),
        reverse=True,
    )


# ── Telegram 通知 ────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram 設定缺失，跳過通知")
        logger.info("通知內容:\n%s", text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram 通知發送成功")
        else:
            logger.error("Telegram 發送失敗: %s %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("Telegram 發送異常: %s", e)


def format_listing_message(listing: dict) -> str:
    price_str = f"{listing['price']:,}" if isinstance(listing['price'], int) else listing['price']
    parts = [
        f"🏠 <b>{listing['title']}</b>",
        f"💰 {price_str} 元/月",
        f"📍 {listing['address']}",
    ]

    if listing.get("area"):
        parts.append(f"📐 {listing['area']}")
    if listing.get("floor"):
        elevator = "有電梯" if listing.get("has_elevator") else "無電梯"
        parts.append(f"🏢 {listing['floor']}（{elevator}）")
    if listing.get("kind_name"):
        parts.append(f"🏷 {listing['kind_name']}")

    parts.append(f"🔗 <a href=\"{listing['url']}\">查看詳情</a>")
    return "\n".join(parts)


# ── 主程式 ───────────────────────────────────────────────
def main():
    tz_tw = timezone(timedelta(hours=8))
    now = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")
    logger.info("=== 591 套房/雅房監控啟動 (%s) ===", now)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            try:
                seen_ids = load_seen_ids()
                logger.info("已記錄 %d 筆歷史房源", len(seen_ids))

                new_listings = []

                for config in SEARCH_CONFIGS:
                    items = fetch_listings_pw(context, config)
                    for item in items:
                        listing = parse_listing(item)
                        if not listing["id"]:
                            continue
                        if listing["id"] in seen_ids:
                            continue
                        # 價格檢查
                        if isinstance(listing["price"], int) and (listing["price"] <= 0 or listing["price"] > 10000):
                            continue
                        # 無電梯且樓層 > 3 則跳過
                        if not listing["has_elevator"] and listing["floor_num"] > 3:
                            continue
                        new_listings.append(listing)
                        seen_ids.add(listing["id"])

                    time.sleep(random.uniform(2.0, 3.0))

                # 合併待推播 + 新房源
                pending = load_pending_listings()
                if pending:
                    logger.info("載入 %d 筆待推播房源", len(pending))

                all_to_send = pending + new_listings
                all_to_send = sort_listings(all_to_send)

                if all_to_send:
                    logger.info("共 %d 筆待推播（新 %d + 上次剩餘 %d）",
                                len(all_to_send), len(new_listings), len(pending))

                    batch = all_to_send[:10]
                    remaining = all_to_send[10:]

                    for listing in batch:
                        msg = format_listing_message(listing)
                        send_telegram(msg)
                        time.sleep(1.1)

                    if remaining:
                        logger.info("剩餘 %d 筆留待下次推播", len(remaining))
                        save_pending_listings(remaining)
                    else:
                        save_pending_listings([])
                else:
                    logger.info("沒有新房源")
                    save_pending_listings([])

                save_seen_ids(seen_ids)
                logger.info("=== 執行完畢 ===")

            except Exception as e:
                logger.error("執行過程發生錯誤: %s", e, exc_info=True)
                send_telegram(f"🚨 591 套房爬蟲執行錯誤\n{e}")
            finally:
                browser.close()

    except Exception as e:
        logger.error("Playwright 啟動失敗: %s", e)
        send_telegram(f"🚨 591 套房爬蟲故障：無法啟動瀏覽器\n{e}")


if __name__ == "__main__":
    main()
