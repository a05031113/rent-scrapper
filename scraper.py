"""
591 租屋監控爬蟲
- 台北市（排除內湖、北投）+ 新北永和、三重
- 整層住家、2房以上、≤30000、有電梯、非頂加、近捷運
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

# 共用搜尋參數
COMMON_PARAMS = {
    "kind": 1,           # 整層住家
    "multiRoom": "2,3,4",  # 2房以上
    "rentprice": "0,30000",
    "other": "lift,not_cover,near_subway",  # 電梯、非頂加、近捷運
    "order": "posttime",   # 最新刊登排序
    "orderType": "desc",
    "firstRow": 0,
    "totalRows": 0,
}

LIST_API = "https://rent.591.com.tw/home/search/rsList"
DETAIL_URL_TPL = "https://rent.591.com.tw/rent-detail-{id}.html"

# ── Helper: 取得 CSRF token + cookies ────────────────────
def get_session() -> requests.Session:
    """建立帶有合法 cookies 和 CSRF token 的 session"""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    })

    # 先造訪首頁取得 cookies（最多重試 3 次）
    resp = None
    for attempt in range(3):
        try:
            resp = sess.get("https://rent.591.com.tw/", timeout=15)
            resp.raise_for_status()
            break
        except Exception as e:
            logger.warning("首頁請求失敗 (第 %d 次): %s", attempt + 1, e)
            if attempt == 2:
                raise RuntimeError(f"無法連線 591 首頁，已重試 3 次: {e}") from e
            time.sleep(2 ** attempt)

    # 從 HTML 中擷取 csrf-token
    html = resp.text
    token = ""
    marker = 'name="csrf-token" content="'
    idx = html.find(marker)
    if idx != -1:
        start = idx + len(marker)
        end = html.find('"', start)
        token = html[start:end]

    if not token:
        # 備援：嘗試從 X-CSRF-TOKEN cookie 取得
        token = sess.cookies.get("X-CSRF-TOKEN", "")

    if token:
        sess.headers["X-CSRF-TOKEN"] = token
        logger.info("CSRF token 取得成功")
    else:
        logger.warning("無法取得 CSRF token，可能影響 API 呼叫")

    return sess


# ── Helper: 設定 region cookie ───────────────────────────
def set_region_cookie(sess: requests.Session, region: int):
    """設定 urlJumpIp cookie 以匹配搜尋 region"""
    sess.cookies.set("urlJumpIp", str(region), domain=".591.com.tw")


# ── 搜尋房源 ─────────────────────────────────────────────
def fetch_listings(sess: requests.Session, config: dict) -> list[dict]:
    """呼叫 591 API 搜尋並回傳所有結果"""
    set_region_cookie(sess, config["region"])

    params = {**COMMON_PARAMS}
    params["region"] = config["region"]
    params["section"] = config["section"]
    params["firstRow"] = 0

    all_items = []
    max_pages = 5  # 安全上限，避免爬太多頁

    for page in range(max_pages):
        params["firstRow"] = page * 30
        logger.info(
            "搜尋 %s | page=%d (firstRow=%d)",
            config["label"], page + 1, params["firstRow"],
        )

        try:
            resp = sess.get(LIST_API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("API 呼叫失敗: %s", e)
            break

        records = data.get("records", "0")
        if isinstance(records, str):
            records = int(records) if records.isdigit() else 0

        items = []
        # 591 API 回傳結構可能是 data.data 或直接 data
        raw_data = data.get("data", {})
        if isinstance(raw_data, dict):
            items = raw_data.get("data", [])
        elif isinstance(raw_data, list):
            items = raw_data

        if not items:
            logger.info("第 %d 頁無資料，結束", page + 1)
            break

        all_items.extend(items)
        logger.info("取得 %d 筆 (累計 %d / %d)", len(items), len(all_items), records)

        # 已經拿完了
        if records > 0 and len(all_items) >= records:
            break

        # 禮貌性延遲 2~4 秒
        delay = random.uniform(2.0, 4.0)
        time.sleep(delay)

    return all_items


# ── 解析單一房源 ─────────────────────────────────────────
def parse_listing(item: dict) -> dict:
    """將 591 API 原始資料轉成統一格式"""
    listing_id = str(item.get("id", item.get("post_id", "")))
    price = item.get("price", "")
    if isinstance(price, str):
        price = price.replace(",", "")
        price = int(price) if price.isdigit() else 0

    return {
        "id": listing_id,
        "title": item.get("title", ""),
        "price": price,
        "address": item.get("address", item.get("location", "")),
        "area": item.get("area", ""),
        "floor": item.get("floor_str", item.get("floor", "")),
        "kind_name": item.get("kind_name", "整層住家"),
        "room": item.get("room", ""),
        "section_name": item.get("section_name", item.get("sectionName", "")),
        "region_name": item.get("region_name", item.get("regionName", "")),
        "url": DETAIL_URL_TPL.format(id=listing_id),
        "photo": item.get("photo_list", [None])[0] if item.get("photo_list") else item.get("cover", ""),
        "post_time": item.get("post_time", item.get("updatetime", "")),
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
        f"📍 {listing.get('region_name', '')} {listing.get('section_name', '')} {listing['address']}",
    ]

    if listing.get("area"):
        parts.append(f"📐 {listing['area']} 坪")
    if listing.get("floor"):
        parts.append(f"🏢 {listing['floor']}")
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
        # 1. 建立 session
        sess = get_session()
        time.sleep(random.uniform(1.0, 2.0))
    except Exception as e:
        logger.error("Session 建立失敗: %s", e)
        send_telegram(f"🚨 591 爬蟲故障：無法建立 session\n{e}")
        return

    try:
        # 2. 載入已看過的 ID
        seen_ids = load_seen_ids()
        logger.info("已記錄 %d 筆歷史房源", len(seen_ids))

        # 3. 搜尋每個區域
        new_listings = []

        for config in SEARCH_CONFIGS:
            items = fetch_listings(sess, config)
            for item in items:
                listing = parse_listing(item)
                if not listing["id"]:
                    continue
                if listing["id"] in seen_ids:
                    continue
                # 雙重確認價格
                if isinstance(listing["price"], int) and (listing["price"] <= 0 or listing["price"] > 30000):
                    continue
                new_listings.append(listing)
                seen_ids.add(listing["id"])

            # 區域之間延遲
            time.sleep(random.uniform(2.0, 3.0))

        # 4. 通知
        if new_listings:
            logger.info("發現 %d 筆新房源！", len(new_listings))

            # 最多一次通知 10 筆，避免洗版
            batch = new_listings[:10]
            for listing in batch:
                msg = format_listing_message(listing)
                send_telegram(msg)
                time.sleep(1.1)  # Telegram rate limit: max 1 msg/sec

            if len(new_listings) > 10:
                send_telegram(f"⚠️ 還有 {len(new_listings) - 10} 筆新房源，請上 591 查看完整列表。")
        else:
            logger.info("沒有新房源")

        # 5. 儲存已看過的 ID
        save_seen_ids(seen_ids)
        logger.info("=== 執行完畢 ===")

    except Exception as e:
        logger.error("執行過程發生錯誤: %s", e, exc_info=True)
        send_telegram(f"🚨 591 爬蟲執行錯誤\n{e}")


if __name__ == "__main__":
    main()
