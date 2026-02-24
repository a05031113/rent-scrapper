# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

591 租屋監控爬蟲 — scrapes Taiwan's 591 rental listing site for new apartments matching predefined criteria, then sends notifications via Telegram Bot. Runs hourly via GitHub Actions (Taiwan time 8:00~23:00).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run scraper (requires env vars)
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python scraper.py

# Run without Telegram (will log notifications to console instead)
python scraper.py
```

## Architecture

Single-file scraper (`scraper.py`) using Playwright to render 591's Nuxt.js SSR pages:

1. **Browser launch** — Playwright Chromium in headless mode
2. **Search** — visits `rent.591.com.tw/list` with query params, extracts listing data from `window.__NUXT__.data` embedded in SSR HTML
3. **Post-filter** — applies filters not available via URL params (elevator/floor, area, layout)
4. **Dedup** — compares results against `seen_ids.json` (persisted between runs)
5. **Merge + Sort** — combines new listings with `pending_listings.json` leftovers, sorts by newest → largest area → lowest rent
6. **Notify** — sends top 10 as HTML-formatted Telegram messages, saves remainder to `pending_listings.json`
7. **Persist** — saves updated seen IDs (capped at 5000 entries)

## Search Filters

### API-level (URL params in `COMMON_PARAMS`)
| Param | Value | Description |
|-------|-------|-------------|
| `kind` | `1` | 整層住家 |
| `layout` | `2,3,4` | 2房以上 |
| `rentprice` | `0,30000` | 月租 ≤ 30,000 |
| `area` | `10,50` | 10~50 坪 |
| `other` | `not_cover,near_subway,cook` | 非頂加、近捷運、可開伙 |
| `option` | `cold,washer,icebox` | 有冷氣、洗衣機、冰箱 |

### Post-filter (in `main()`)
| Filter | Logic |
|--------|-------|
| Elevator/floor | 無電梯且樓層 > 3F → 跳過 |
| Open layout | `layoutStr` 含「開放式」→ 跳過 |
| Min area | `area_num` < 15 → 跳過 |

## Key Configuration

- **SEARCH_CONFIGS** (line ~45): region/section combos — 台北市（排除內湖/北投）、新北永和、新北三重
- **COMMON_PARAMS** (line ~65): shared 591 URL query params
- **SEEN_FILE**: `seen_ids.json` — already-notified listing IDs, auto-committed by GitHub Actions
- **PENDING_FILE**: `pending_listings.json` — listings queued for next run's notification batch

## Key Files

| File | Purpose |
|------|---------|
| `scraper.py` | Main scraper logic |
| `seen_ids.json` | Persisted set of notified listing IDs |
| `pending_listings.json` | Overflow listings for next batch |
| `.github/workflows/scrape.yml` | GitHub Actions workflow |
| `requirements.txt` | Python dependencies (requests, playwright) |

## Deployment

GitHub Actions workflow at `.github/workflows/scrape.yml`:
- **Schedule**: hourly, Taiwan time 8:00~23:00 (cron `0 0-15 * * *`)
- **Secrets required**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (use `-100` prefix for supergroup/channel IDs)
- Auto-commits `seen_ids.json` and `pending_listings.json` after each run

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | For notifications | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | For notifications | Target chat/channel ID (supergroup needs `-100` prefix) |
