# Daily Market Brief

A self-publishing market dashboard. A Python script fetches market data, computes
statistics that are not available at a glance elsewhere, and writes a static HTML
page. GitHub Actions runs it on a schedule; GitHub Pages serves the result.

**Live page:** https://firdevssaygin.github.io/market-brief/

## What it shows

- **Was today unusual?** Each fund's move as a z-score against its own past year,
  alongside annualised volatility, that volatility's percentile rank, and
  drawdown from the 52-week high. A 2% move means nothing until you know the
  fund's normal daily range.
- **Do yields still move QQQ?** A rolling correlation between the fund's daily
  returns and daily changes in the 10-year Treasury yield, so the textbook claim
  that rising yields pressure technology stocks is measured rather than assumed.
- **Prices and returns** for SPY, QQQ, DIA, IWM, SOXX and XLE, plus charts of the
  day's moves and a rebased SPY vs QQQ trend.
- **Upcoming US releases** from a hand-maintained calendar, each with what a
  higher- or lower-than-expected reading tends to mean.
- **Headlines** from the Federal Reserve and CNBC: headline, source, date and
  link only.

## Design rules

1. **No invented numbers.** Every figure is computed from data downloaded during
   that run. Explanatory text is fixed commentary and deliberately contains no
   figures, so it can never contradict a fresh number. Anything that cannot be
   computed prints `n/a`.
2. **Free sources only.** No API keys, no secrets, no paid services.
3. **No server.** The page is static; all computation happens before publication.
4. **Few files, all readable.** One script, one calendar, one page.

## Files

| File | Purpose |
|---|---|
| `brief.py` | Everything: fetching, statistics, charts, page generation |
| `calendar.json` | Hand-maintained economic calendar (the only file edited by hand) |
| `requirements.txt` | Packages to install |
| `docs/index.html` | The generated page, served by GitHub Pages |

## Running it locally

```bash
pip install -r requirements.txt
python3 brief.py
open docs/index.html
```

The script prints the same figures to the terminal that it writes to the page, so
the two can be checked against each other.

## Data sources

Prices and yields from Yahoo Finance via `yfinance`. Headlines from the Federal
Reserve and CNBC RSS feeds via `feedparser`. Calendar dates are maintained by hand
and flagged as unconfirmed until verified against the issuing agency.

Statistics here are descriptive summaries of past data and say nothing about what
happens next. Not investment advice.
