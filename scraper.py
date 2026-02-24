"""
591 租屋監控爬蟲
- 台北市（排除內湖、北投）+ 新北永和、三重
- 整層住家、2房以上、≤30000、10~50坪、非頂加、近捷運、可開伙
- 設備：冷氣、洗衣機、冰箱 ｜ 無電梯限3樓以下
- Telegram Bot 通知
- GitHub Actions 定時執行

591 已改為 Nuxt.js SSR 架構，搜尋結果內嵌於 __NUXT__.data，
因此改用 Playwright 渲染頁面後從 JS context 擷取資料。
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

# 已通知過的房源 ID 檔案路徑
SEEN_FILE = Path(__file__).parent / "seen_ids.json"
PENDING_FILE = Path(__file__).parent / "pending_listings.json"

# ── 591 區域 / 行政區 ID 對照 ────────────────────────────
# 台北市 region=1
#   1=中正 2=大同 3=中山 4=松山 5=大安
#   6=萬華 7=信義 8=士林 9=北投 10=內湖
#   11=南港 12=文山
# 新北市 region=3
#   37=永和 43=三重

SEARCH_CONFIGS = [
    {
        "label": "台北市（排除內湖/北投）",
        "region": 1,
        "section": "1,2,3,4,5,6,7,8,11,12",
    },
    {
        "label": "新北永和區",
        "region": 3,
        "section": "37",
    },
    {
        "label": "新北三重區",
        "region": 3,
        "section": "43",
    },
]

# 共用搜尋 URL 參數（對應 591 Nuxt SSR 路由）
COMMON_PARAMS = {
    "kind": "1",              # 整層住家
    "layout": "2,3,4",        # 2房以上
    "rentprice": "0,30000",
    "area": "10,50",           # 10~50坪
    "other": "not_cover,near_subway,cook",  # 非頂加、近捷運、可開伙（電梯改為後篩選）
    "option": "cold,washer,icebox",         # 有冷氣、有洗衣機、有冰箱
    "order": "posttime",       # 最新刊登排序
    "orderType": "desc",
}

BASE_URL = "https://rent.591.com.tw/list"

# JS 腳本：從 __NUXT__.data 擷取搜尋結果
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
    """用 Playwright 造訪搜尋頁面，從 __NUXT__ 擷取房源列表"""
    params = {**COMMON_PARAMS}
    params["region"] = str(config["region"])
    params["section"] = config["section"]

    all_items: list[dict] = []
    max_pages = 5

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

            # 從 __NUXT__ 擷取資料
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

            # 禮貌性延遲
            time.sleep(random.uniform(2.0, 4.0))
    finally:
        page.close()

    return all_items


# ── 解析單一房源 ─────────────────────────────────────────
def _parse_floor(floor_name: str) -> int:
    """從 '4F/8F' 格式取得所在樓層數字，解析失敗回傳 0"""
    if not floor_name:
        return 0
    part = floor_name.split("/")[0].strip().upper()
    # 處理 B1F 等地下室
    if part.startswith("B"):
        return 0
    digits = "".join(c for c in part if c.isdigit())
    return int(digits) if digits else 0


def parse_listing(item: dict) -> dict:
    """將 591 Nuxt SSR 資料轉成統一格式"""
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
        "kind_name": item.get("kind_name", "整層住家"),
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
    # 只保留最近 5000 筆，避免檔案無限成長
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
    """排序：上架時間（ID 大 = 新）> 坪數大 > 租金低"""
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
    """格式化單一房源為 Telegram HTML 訊息"""
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
    if listing.get("room"):
        parts.append(f"🛏 {listing['room']}")

    parts.append(f"🔗 <a href=\"{listing['url']}\">查看詳情</a>")
    return "\n".join(parts)


# ── 主程式 ───────────────────────────────────────────────
def main():
    tz_tw = timezone(timedelta(hours=8))
    now = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")
    logger.info("=== 591 租屋監控啟動 (%s) ===", now)

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
                # 1. 載入已看過的 ID
                seen_ids = load_seen_ids()
                logger.info("已記錄 %d 筆歷史房源", len(seen_ids))

                # 2. 搜尋每個區域
                new_listings = []

                for config in SEARCH_CONFIGS:
                    items = fetch_listings_pw(context, config)
                    for item in items:
                        listing = parse_listing(item)
                        if not listing["id"]:
                            continue
                        if listing["id"] in seen_ids:
                            continue
                        # 雙重確認價格
                        if isinstance(listing["price"], int) and (listing["price"] <= 0 or listing["price"] > 30000):
                            continue
                        # 無電梯且樓層 > 3 則跳過
                        if not listing["has_elevator"] and listing["floor_num"] > 3:
                            continue
                        # 排除開放式格局
                        if listing.get("room") and "開放式" in listing["room"]:
                            continue
                        # 坪數至少 15 坪
                        if listing.get("area_num", 0) < 15:
                            continue
                        new_listings.append(listing)
                        seen_ids.add(listing["id"])

                    # 區域之間延遲
                    time.sleep(random.uniform(2.0, 3.0))

                # 3. 合併待推播 + 新房源，排序後推播前 10 筆
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
                        time.sleep(1.1)  # Telegram rate limit

                    if remaining:
                        logger.info("剩餘 %d 筆留待下次推播", len(remaining))
                        save_pending_listings(remaining)
                    else:
                        save_pending_listings([])
                else:
                    logger.info("沒有新房源")
                    save_pending_listings([])

                # 4. 儲存已看過的 ID
                save_seen_ids(seen_ids)
                logger.info("=== 執行完畢 ===")

            except Exception as e:
                logger.error("執行過程發生錯誤: %s", e, exc_info=True)
                send_telegram(f"🚨 591 爬蟲執行錯誤\n{e}")
            finally:
                browser.close()

    except Exception as e:
        logger.error("Playwright 啟動失敗: %s", e)
        send_telegram(f"🚨 591 爬蟲故障：無法啟動瀏覽器\n{e}")


if __name__ == "__main__":
    main()
