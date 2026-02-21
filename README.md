# 591 租屋監控爬蟲

自動爬取 591 租屋網新物件，透過 Telegram Bot 即時通知。

## 搜尋條件

| 條件 | 設定 |
|------|------|
| 區域 | 台北市（排除內湖、北投）、新北永和、新北三重 |
| 類型 | 整層住家 |
| 房間 | 2 房以上 |
| 租金 | ≤ 30,000 元/月 |
| 電梯 | 必須有 |
| 頂加 | 排除 |
| 捷運 | 需近捷運站 |

## 部署步驟

### 1. 建立 Telegram Bot

1. 在 Telegram 搜尋 `@BotFather`
2. 傳送 `/newbot`，依指示建立 Bot
3. 記下 Bot Token
4. 對 Bot 傳送任意訊息，然後開啟 `https://api.telegram.org/bot<TOKEN>/getUpdates` 取得 chat_id

### 2. 設定 GitHub Secrets

在 GitHub repo → Settings → Secrets and variables → Actions 中新增：

- `TELEGRAM_BOT_TOKEN`: Bot Token
- `TELEGRAM_CHAT_ID`: Chat ID

### 3. 啟用 GitHub Actions

Push 到 GitHub 後，Actions 會自動依排程執行（台灣時間 8:00, 12:00, 18:00, 22:00）。

也可以在 Actions 頁面手動觸發 `workflow_dispatch`。

## 本機測試

```bash
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
pip install -r requirements.txt
python scraper.py
```

## 檔案說明

- `scraper.py` — 主程式
- `seen_ids.json` — 已通知過的房源 ID（由 GitHub Actions 自動 commit）
- `.github/workflows/scrape.yml` — GitHub Actions 排程
