# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

591 租屋監控爬蟲 — scrapes Taiwan's 591 rental listing site for new apartments matching predefined criteria, then sends notifications via Telegram Bot. Runs on a schedule via GitHub Actions (4x daily, Taiwan time 8:00/12:00/18:00/22:00).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run scraper (requires env vars)
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python scraper.py

# Run without Telegram (will log notifications to console instead)
python scraper.py
```

## Architecture

Single-file scraper (`scraper.py`) with this flow:

1. **Session setup** — visits 591 homepage to get cookies + CSRF token
2. **Search** — queries `rent.591.com.tw/home/search/rsList` API for each region config (Taipei minus Neihu/Beitou, New Taipei Yonghe, New Taipei Sanchong)
3. **Dedup** — compares results against `seen_ids.json` (persisted between runs)
4. **Notify** — sends new listings as HTML-formatted Telegram messages (max 10 per run)
5. **Persist** — saves updated seen IDs (capped at 5000 entries)

## Key Configuration

- **SEARCH_CONFIGS** (line 42): region/section combos defining search areas
- **COMMON_PARAMS** (line 61): shared 591 API query params (rent cap, room count, elevator, etc.)
- **SEEN_FILE**: `seen_ids.json` in project root, auto-committed by GitHub Actions

## Deployment

GitHub Actions workflow is at `.github/workflows/scrape.yml`. Requires two GitHub Secrets: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The workflow auto-commits `seen_ids.json` after each run with `git pull --rebase` to handle concurrent runs.

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | For notifications | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | For notifications | Target chat/group ID |
