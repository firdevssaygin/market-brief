"""Daily market brief.

An analysis tool, not a data display. Run it and it writes docs/index.html.

WHY THIS EXISTS AT ALL
  Closing prices are one click away on any finance site. What is NOT one click
  away is context: what kind of market this is, whether today's move was
  statistically unusual, whether the textbook relationship between bond yields
  and technology stocks is actually holding right now, and where each fund sits
  in its own volatility and drawdown history. All of that needs a year of
  history and some arithmetic on top of it, which is what this script does.

THE ONE RULE THIS FILE ENFORCES
  Numbers come from data that was actually downloaded. Text in EXPLANATIONS is
  hand-written commentary and is deliberately qualitative - it contains no
  figures at all, so a stale explanation can never contradict a fresh number.
  If a figure cannot be computed, the page says "n/a" rather than guessing.

A NOTE FOR THE R USER
  pandas is Python's data.frame library. Rough translations:
    library(x)            ->  import x
    install.packages("x") ->  pip install x
    df$Close              ->  df["Close"]
    tail(df$Close, 1)     ->  df["Close"].iloc[-1]     (-1 = last; R has no
                                                        negative indexing like
                                                        this, -1 in R DROPS)
    subset(df, date <= d) ->  df[df.index <= d]        (same boolean-mask idea
                                                        as dplyr::filter)
    diff(x) / x[-1]       ->  x.diff() / x.pct_change()
    sd(x); mean(x)        ->  x.std(); x.mean()
    merge(a, b)           ->  pd.concat([a, b], axis=1, join="inner")
    list(a = 1, b = 2)    ->  {"a": 1, "b": 2}         (a "dict", a named list)
    NULL / NA             ->  None
    sprintf("%.2f", x)    ->  f"{x:.2f}"
  One real difference: pandas rows carry an *index* (here, the date), like
  rownames() but taken seriously - pandas uses it to line up two series when
  you combine them. That is powerful and it is also a trap; see dated_series().
"""

import io                         # lets pandas read text as if it were a file
import json                       # reads calendar.json
import sys                        # lets the script report failure to GitHub
from datetime import date, datetime, timedelta
from html import escape           # makes outside text safe to place in a web page
from math import sqrt
from pathlib import Path          # a tidier way to handle file paths than raw text
from zoneinfo import ZoneInfo     # timezone support, built into Python

import feedparser                 # understands RSS news feeds
import pandas as pd
import plotly.graph_objects as go
import requests                   # downloads those feeds
import yfinance as yf
from plotly.offline import get_plotlyjs_version

# The charting library is loaded once, in the page's <head>, from a free public
# CDN. Asking plotly which version it generated code for keeps the two in step;
# hard-coding a version number here would silently rot when plotly updates.
PLOTLY_JS_URL = f"https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"


# ---------------------------------------------------------------------------
# SETTINGS - the only part of this file you should need to edit
# ---------------------------------------------------------------------------

TICKERS = ["SPY", "QQQ", "DIA", "IWM", "SOXX", "XLE"]

# Yahoo's symbol for the US 10-year Treasury yield.
YIELD_TICKER = "^TNX"

# Which fund gets tested against the yield, and which two the trend chart compares.
YIELD_PAIR_TICKER = "QQQ"
TREND_TICKERS = ["SPY", "QQQ"]
TREND_DAYS = 91                  # roughly three months

# Statistical windows, in trading days.
VOL_WINDOW = 20                  # ~one month, the standard short-run vol window
CORR_WINDOW = 60                 # ~three months, long enough to be stable
TRADING_DAYS_PER_YEAR = 252      # the convention for annualising daily volatility

# Which column the bar chart shows. Swap for "change_1w" or "change_ytd".
BAR_COLUMN = "change_1d"
BAR_LABELS = {"change_1d": "1-day", "change_1w": "1-week", "change_ytd": "year-to-date"}

# Where the finished page is written. __file__ is this script's own location,
# so the script works no matter which folder you run it from.
OUTPUT_PATH = Path(__file__).parent / "docs" / "index.html"

TIMEZONE = ZoneInfo("Europe/Istanbul")

# News feeds: (name shown on the page, feed address, how many days back to look).
# All free, none need a key. The Fed feed is its monetary-policy releases rather
# than every press release, which keeps bank-merger approvals out of a markets
# brief.
#
# The age limit differs per feed on purpose. The Fed publishes when it has
# something to say - often nothing for weeks - so a short window would leave its
# card permanently empty and make a quiet central bank look like a broken feed.
# A news wire publishes hourly, so anything over a week old there is stale.
# Each headline carries its date, so you can always see how old the news is.
FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", 90),
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html", 7),
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html", 7),
]
HEADLINES_PER_FEED = 6
FEED_TIMEOUT_SECONDS = 20

# The hand-maintained calendar, and how many events to show.
CALENDAR_PATH = Path(__file__).parent / "calendar.json"
CALENDAR_EVENTS_SHOWN = 6

# Your holdings. Two possible sources.
#
# If POSITIONS_CSV_URL is filled in, the holdings are read from a published
# Google Sheet, so you can edit them from a phone without touching GitHub. If it
# is left empty, positions.json is used instead. The sheet wins when both exist.
POSITIONS_PATH = Path(__file__).parent / "positions.json"
# TradingView writes symbols differently again: it wants an exchange prefix.
# Yahoo says AMD, TradingView says NASDAQ:AMD; Yahoo says CHZ-USD, and since you
# trade on OKX the matching TradingView symbol is OKX:CHZUSDT. Anything not
# listed here falls back to the bare symbol, which TradingView usually resolves
# on its own. Add an entry if a chart ever shows the wrong instrument.
TRADINGVIEW_SYMBOLS = {
    "AMD": "NASDAQ:AMD",
    "MP": "NYSE:MP",
    "LITE": "NASDAQ:LITE",
}

POSITIONS_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTOB5Zv19hu0C36dJqp4UN5"
    "-V1Y6v2t_vLbzub2mMgqd8RdWh7Tp6KB02aT0gvInl7s0HacypbTzNVA/pub?output=csv"
)

# --- Risk regime -----------------------------------------------------------
# Four free indicators that describe the environment rather than any one fund.
VIX_TICKER = "^VIX"              # expected volatility of the S&P 500
BILL_TICKER = "^IRX"             # 13-week Treasury bill yield, the short end
HIGH_YIELD_TICKER = "HYG"        # high-yield ("junk") corporate bonds
INVESTMENT_GRADE_TICKER = "LQD"  # investment-grade corporate bonds
DOLLAR_TICKER = "DX-Y.NYB"       # ICE US Dollar Index

# How far back the credit and dollar comparisons look, in trading days.
# 63 trading days is about three calendar months.
REGIME_LOOKBACK_DAYS = 63

# The risk watcher scores each indicator by its percentile rank over a trailing
# year, then averages the four. Two years of history are downloaded so that a
# full year of scores can be computed - each day needs the year before it.
RISK_SCORE_WINDOW = 252
REGIME_HISTORY_PERIOD = "2y"

# An indicator whose newest reading is more than this many days behind the ETF
# data is treated as stale and labelled on the page instead of shown as current.
#
# This exists because of a real near-miss. Yahoo's ^VIX3M series stopped
# updating on 2026-07-17 while ^VIX carried on, so a VIX term-structure ratio
# would have divided a fresh number by a three-week-old one and printed a
# confident, meaningless answer. A number that is quietly out of date is more
# dangerous than one that is obviously missing.
STALE_AFTER_DAYS = 5


# ---------------------------------------------------------------------------
# COLOURS - checked with a colourblindness validator, not chosen by eye
# ---------------------------------------------------------------------------
# Deliberately NOT the usual green-up / red-down: red-green is exactly the pair
# that roughly 1 in 12 men cannot separate. Blue-up / red-down carries the same
# meaning and stays readable for everyone.

COLOR_UP = "#2a78d6"       # blue
COLOR_DOWN = "#e34948"     # red
COLOR_SERIES_2 = "#eb6834"  # orange, for the second line on the trend chart
COLOR_SURFACE = "#fcfcfb"
COLOR_INK = "#0b0b0b"
COLOR_MUTED = "#898781"
COLOR_GRID = "#e1e0d9"

# Status colours, kept separate from the series colours above so a state can
# never be mistaken for a data series. Each is always shown WITH a word - "calm",
# "stressed" - because two of them are too light to carry meaning by colour alone,
# and because a reader who cannot separate the hues still needs the reading.
COLOR_CALM = "#0ca30c"
COLOR_NORMAL = "#898781"
COLOR_CAUTION = "#ec835a"
COLOR_STRESSED = "#d03b3b"

# Where each band starts, and what to call it. Edit these and everything - the
# gauge, the wording, the colours, the history chart - follows.
RISK_BANDS = [
    (0, "Calm", COLOR_CALM),
    (30, "Normal", COLOR_NORMAL),
    (60, "Caution", COLOR_CAUTION),
    (80, "Stressed", COLOR_STRESSED),
]


# ---------------------------------------------------------------------------
# COMMENTARY - static, qualitative, and never contains a number
# ---------------------------------------------------------------------------

EXPLANATIONS = {
    "yield": (
        "The 10-year Treasury yield is the market's baseline for pricing almost "
        "everything else. A share is worth the cash it will produce in future, "
        "discounted back to today; the yield is a large part of that discount rate. "
        "When it rises, future cash is worth less now - and the further out the cash "
        "sits, the more it is marked down. Growth and technology names earn much of "
        "their value from distant profits, so they carry longer 'duration' and tend to "
        "react more sharply than an index of established, cash-generating firms. "
        "The correlation card below tests whether that textbook story is actually "
        "holding at the moment, rather than asking you to take it on trust."
    ),
    "risk": (
        "This is the table you cannot get in one click. A percentage move means "
        "nothing until you know the fund's normal daily range, so each move is also "
        "shown as a z-score: how many standard deviations it sits from that fund's "
        "average day over the past year. Roughly speaking, most days land inside one "
        "standard deviation and anything past two is worth a second look. "
        "But treat those thresholds loosely - market returns have fat tails, meaning "
        "extreme days occur far more often than the normal distribution predicts. "
        "That gap between the bell curve and reality is where a great deal of "
        "risk-management practice lives, and where a great deal of money has been lost "
        "by people who assumed otherwise. "
        "Volatility is the annualised standard deviation of recent daily moves; its "
        "percentile says where that sits against the fund's own past year, which is "
        "what distinguishes a calm market from a stressed one. Drawdown measures the "
        "fall from the highest close of the past year - closing prices only, so a "
        "spike low during the trading day is not captured."
    ),
    "correlation": (
        "This measures whether the yield story above is currently true, using a "
        "rolling window of recent trading days. Each day it asks: when the 10-year "
        "yield rose, did the fund tend to fall? A negative reading means yes - the "
        "textbook relationship is holding. Near zero means yields simply are not "
        "what is driving equities at the moment. Positive means they are rising and "
        "falling together, which typically happens when both are responding to "
        "stronger growth expectations rather than to inflation or policy fears. "
        "Three warnings. Correlation is not causation: both series can be responding "
        "to a third thing entirely. A rolling window is noisy, and one dramatic day "
        "entering or leaving the window can swing the line visibly. And a "
        "relationship that holds for months can break in a week - which is precisely "
        "why it is worth watching a measurement rather than memorising a rule."
    ),
    "bars": (
        "One session's move is mostly noise. Its use is spotting dispersion: when the "
        "broad market barely moves while a sector fund swings hard, the day's story is "
        "a rotation between sectors rather than a change in overall risk appetite. "
        "Compare the spread between the bars, not the height of any one."
    ),
    "trend": (
        "Both lines start at 100 so they can share one axis. This matters: plotting "
        "two prices at different levels on two different y-axes lets you align the "
        "scales to manufacture almost any correlation you like, which is why "
        "dual-axis charts are the single most misleading chart in finance. Rebased "
        "to a common start, the vertical gap between the lines is the actual "
        "difference in return since that date, and nothing else."
    ),
    "regime": (
        "These four say nothing about any single fund. They describe the weather - "
        "and the same position can be sensible in one environment and reckless in "
        "another, so this is the panel to read first. "
        "<b>VIX</b> is what option prices imply about how much the S&P 500 will move "
        "over the coming month; its percentile matters more than its level, because "
        "what counts as a high reading differs between a calm year and a turbulent "
        "one. <b>The yield curve</b> compares the 10-year yield with the 3-month "
        "bill: normally longer money pays more, and when it does not - an inversion - "
        "the market is saying it expects rates to be cut, usually because it expects "
        "trouble. Inversions have preceded most post-war US recessions, but with lags "
        "measured in quarters and several false alarms, so it is a warning light, "
        "never a timing signal. <b>Credit</b> compares high-yield bonds with "
        "investment-grade ones: when the riskier of the two starts lagging, lenders "
        "are pricing more defaults, and credit markets have a long record of noticing "
        "before equity markets do. <b>The dollar</b> sets global financial conditions "
        "- a strengthening dollar tightens them everywhere, and tends to weigh on "
        "commodities and emerging markets first."
    ),
    "watcher": (
        "One number for the market's weather, built by scoring each indicator "
        "below from 0 to 100 and averaging them. A score is that indicator's "
        "percentile rank over the trailing year: 90 means it sits higher than "
        "ninety percent of the past year, and every indicator is oriented so that "
        "a higher score always means more risk. "
        "Be clear about what this is. It is a <b>construction, not a measurement</b> "
        "- four indicators equally weighted because there is no principled reason "
        "to prefer one, and a different analyst would build a different index and "
        "be equally entitled to. That is why the four components are always shown "
        "beneath it: when the headline and its parts disagree, the parts are the "
        "more informative reading. "
        "Two limits worth holding onto. It is <b>relative to the past year only</b>, "
        "so a calm year still produces a high score at its own worst moment - the "
        "score says 'unusual for recently', never 'dangerous in absolute terms'. "
        "And it describes conditions that already exist; markets turn before the "
        "indicators that describe them do, so this is a thermometer, not a forecast."
    ),
    "curve": (
        "The gap between the 10-year yield and the 3-month bill, in basis points. "
        "Above the line, longer borrowing costs more than short, which is the normal "
        "state of affairs. Below it, the curve is inverted. Watch the direction of "
        "travel as much as the level: a curve steepening back through zero after an "
        "inversion has historically been closer to trouble than the inversion itself, "
        "because it usually means cuts have started."
    ),
    "portfolio": (
        "Two numbers here are worth more than the profit figure. "
        "<b>Portfolio volatility</b> is not the average of your holdings' "
        "volatilities - it is calculated from how they actually move together, "
        "using the covariance between every pair. Two holdings that rise and fall "
        "in step are nearly one holding; two that move independently partly cancel "
        "each other out. The gap between the true figure and the naive weighted "
        "average is the <b>diversification benefit</b>: real risk reduction you get "
        "for free, measured rather than assumed. "
        "The correlation grid shows where it comes from. Holdings with high "
        "correlation are the same bet under two names, however different the "
        "companies look - and correlations rise toward one during a crisis, exactly "
        "when the protection matters most, so treat calm-period diversification as "
        "the optimistic case. "
        "Everything is converted to one currency at the live rate before being "
        "added up. Profit is against your stated buy price and ignores commission, "
        "spread and tax, so it is the gross figure, not what you would actually "
        "walk away with."
    ),
    "tradingview": (
        "Everything else on this page is computed when the page is built and then "
        "frozen: open it a week from now with no internet and the numbers are still "
        "there, exactly as they were. These two panels are different. They are "
        "TradingView's own code, fetching TradingView's own data in your browser as "
        "you look at them. "
        "That is a deliberate trade. It buys intraday movement that a page rebuilt "
        "every three hours cannot show, and an economic calendar maintained by people "
        "whose job it is - which is why the hand-written calendar above now carries "
        "the interpretation rather than the dates. The cost is that these are the only "
        "parts of the page that depend on someone else's service being up, and the "
        "only parts that watch you back: TradingView sets its own cookies here."
    ),
    "headlines": (
        "Headline, source, date and link only - no summaries and no article text. "
        "That is partly a copyright matter (an RSS feed invites you to link to a "
        "story, not to republish it) and partly a research habit worth keeping: a "
        "summarised headline is one you can no longer check, and the compression "
        "step is exactly where a nuance quietly goes missing. Follow the link for "
        "anything that matters."
    ),
    "calendar": (
        "Scheduled releases are the days markets have already agreed to care about, "
        "which is what makes them tradeable events rather than news. What moves "
        "prices is never the number itself but the gap between the number and what "
        "was expected - a strong reading that everyone already anticipated moves "
        "nothing at all. That is why each entry below reads in both directions "
        "rather than declaring one outcome good. Dates marked unconfirmed are "
        "pattern-based estimates and need checking against the official source "
        "before you rely on them."
    ),
    "returns": (
        "These are price returns: they exclude dividends. Total return - what you "
        "would actually have earned holding the fund - is higher, and the gap is "
        "wider for income-heavy sectors such as energy than for technology."
    ),
}


# ---------------------------------------------------------------------------
# FETCHING
# ---------------------------------------------------------------------------

def percent_change(new_price, old_price):
    """How far new_price sits above (+) or below (-) old_price, in percent."""
    return (new_price - old_price) / old_price * 100


def close_on_or_before(history, cutoff_date):
    """The closing price from the newest trading day at or before cutoff_date.

    Markets shut at weekends and on holidays, so the exact date we ask for often
    has no row. Rather than invent a price for a day that never traded, step back
    to the most recent day that did. Returns None if the table starts too late.
    """
    rows_up_to_cutoff = history[history.index <= cutoff_date]
    if rows_up_to_cutoff.empty:
        return None
    return rows_up_to_cutoff["Close"].iloc[-1]


def download_history(ticker, period="1y"):
    """Download daily bars, or return None if nothing came back.

    auto_adjust=False keeps the raw closing price - the number quoted on a
    finance site - which is what makes these price returns rather than total
    returns.
    """
    history = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    if history.empty:
        return None
    return history


def fetch_ticker(ticker):
    """One ETF's latest close, its changes, and the raw history for the maths.

    The full year of history is kept in the result so the statistics below can
    reuse it. Downloading once and computing many things from it is both faster
    and kinder to Yahoo's servers than fetching again per calculation.
    """
    history = download_history(ticker)
    if history is None:
        return None

    latest_date = history.index[-1]
    latest_close = history["Close"].iloc[-1]

    previous_close = close_on_or_before(history, latest_date - timedelta(days=1))
    week_ago_close = close_on_or_before(history, latest_date - timedelta(days=7))

    # Year-to-date is measured from the final close of LAST year, the standard
    # convention. Keep the rows from earlier calendar years and take the last.
    previous_years = history[history.index.year < latest_date.year]
    year_start_close = None if previous_years.empty else previous_years["Close"].iloc[-1]

    return {
        "ticker": ticker,
        "history": history,
        "date": latest_date,
        "close": latest_close,
        # "value if condition else other" is Python's ifelse(); None means
        # "we could not compute this", and prints as n/a.
        "change_1d": percent_change(latest_close, previous_close) if previous_close else None,
        "change_1w": percent_change(latest_close, week_ago_close) if week_ago_close else None,
        "change_ytd": percent_change(latest_close, year_start_close) if year_start_close else None,
    }


def fetch_yield():
    """The 10-year Treasury yield, its recent moves, and its history.

    Yahoo quotes ^TNX directly in percent (a value of 4.66 means 4.66%). Yields
    are compared in BASIS POINTS, not percent change: one basis point is a
    hundredth of a percentage point. A move from 4.60% to 4.66% is "up 6bp".
    Saying it "rose 1.3%" would be technically true of the number and useless to
    anyone in markets, so we report the difference, not the ratio.
    """
    history = download_history(YIELD_TICKER)
    if history is None:
        return None

    latest_date = history.index[-1]
    latest = history["Close"].iloc[-1]
    previous = close_on_or_before(history, latest_date - timedelta(days=1))
    week_ago = close_on_or_before(history, latest_date - timedelta(days=7))

    return {
        "history": history,
        "date": latest_date,
        "level": latest,
        "change_1d_bp": (latest - previous) * 100 if previous else None,
        "change_1w_bp": (latest - week_ago) * 100 if week_ago else None,
    }


# ---------------------------------------------------------------------------
# STATISTICS
# ---------------------------------------------------------------------------

def dated_series(series):
    """Re-label a series by plain calendar date, so two series can be lined up.

    THIS IS NOT COSMETIC. Yahoo timestamps ETF bars in New York time and ^TNX
    bars in Chicago time. Same trading day, different clock, so pandas treats
    them as different labels: joining them raw matches ZERO rows and every
    correlation silently comes out empty. Stripping the clock and keeping only
    the date fixes it. Whenever you combine two data sources, check that the
    join actually matched something - a join that quietly matches nothing is far
    more dangerous than one that crashes.
    """
    relabelled = series.copy()
    relabelled.index = relabelled.index.tz_localize(None).normalize()
    return relabelled


def daily_returns(history):
    """Daily percentage returns, labelled by date. pct_change() is R's diff(x)/lag(x)."""
    return dated_series(history["Close"]).pct_change().dropna() * 100


def percentile_rank(series, value):
    """What share of the series sits at or below `value`, as a percentage.

    A comparison like (series <= value) gives a column of True/False, and the
    mean of that column is the proportion that are True - the same trick as
    mean(x <= value) in R.
    """
    if series.empty:
        return None
    return (series <= value).mean() * 100


def compute_risk(row):
    """Z-score of today's move, current volatility and its rank, and drawdown."""
    returns = daily_returns(row["history"])

    # Not enough history to say anything honest about the distribution.
    if len(returns) < VOL_WINDOW + 2:
        return None

    latest_return = returns.iloc[-1]

    # Compare today against the days BEFORE today, so the day being judged is
    # not also part of the yardstick judging it.
    prior_returns = returns.iloc[:-1]
    average = prior_returns.mean()
    deviation = prior_returns.std()
    z_score = (latest_return - average) / deviation if deviation else None

    # Annualised volatility: the standard deviation of daily moves, scaled up by
    # the square root of the number of trading days in a year. The square root
    # appears because variance adds over time while standard deviation does not.
    volatility_series = (returns.rolling(VOL_WINDOW).std() * sqrt(TRADING_DAYS_PER_YEAR)).dropna()
    current_volatility = volatility_series.iloc[-1] if not volatility_series.empty else None

    # Drawdown from the highest CLOSE of the past year (not the intraday high).
    year_high = row["history"]["Close"].max()
    drawdown = percent_change(row["close"], year_high)

    return {
        "ticker": row["ticker"],
        "change_1d": row["change_1d"],
        "z_score": z_score,
        "volatility": current_volatility,
        "volatility_percentile": percentile_rank(volatility_series, current_volatility)
        if current_volatility is not None else None,
        "drawdown": drawdown,
    }


def rolling_yield_correlation(equity_history, yield_history, window):
    """Rolling correlation between a fund's daily returns and daily yield changes.

    Returns a series of correlations (one per day) or None if the two data sets
    could not be lined up.
    """
    equity_returns = daily_returns(equity_history)

    # The yield's DAILY CHANGE in basis points - the difference between one day
    # and the next, not a percentage change. diff() is R's diff().
    yield_changes = dated_series(yield_history["Close"]).diff().dropna() * 100

    # join="inner" keeps only dates present in both, like merge() without all=TRUE.
    combined = pd.concat(
        [equity_returns.rename("equity"), yield_changes.rename("yield")],
        axis=1, join="inner",
    ).dropna()

    # Guard against the silent-empty-join failure described in dated_series().
    if len(combined) < window + 5:
        return None

    correlation = combined["equity"].rolling(window).corr(combined["yield"])
    correlation = correlation.dropna()
    return correlation if not correlation.empty else None


def rebased_trend(rows, tickers, days):
    """Closing prices for the trend chart, each rebased to start at 100.

    Reuses the year of history already downloaded rather than fetching again.
    Dividing a whole column by its first value rescales every element at once,
    exactly as prices / prices[1] * 100 would in R.
    """
    by_ticker = {}
    for row in rows:
        if row is None or row["ticker"] not in tickers:
            continue
        history = row["history"]
        window_start = history.index[-1] - timedelta(days=days)
        closes = history[history.index >= window_start]["Close"]
        if closes.empty:
            continue
        by_ticker[row["ticker"]] = closes / closes.iloc[0] * 100
    return by_ticker


# ---------------------------------------------------------------------------
# RISK REGIME
# ---------------------------------------------------------------------------

def fetch_closes(ticker, period="1y"):
    """Closing prices labelled by plain date, with gaps removed.

    .dropna() throws away days where Yahoo has a hole in the series. Without it,
    "the last row" can be an empty value that quietly poisons every calculation
    downstream - which is exactly what ^VIX3M does.
    """
    history = download_history(ticker, period=period)
    if history is None:
        return None
    closes = dated_series(history["Close"]).dropna()
    return closes if not closes.empty else None


def days_behind(series, reference_date):
    """How many days old the newest reading is, compared with the ETF data."""
    return (reference_date - series.index[-1].date()).days


def change_over(series, trading_days):
    """Percentage change against the value `trading_days` rows earlier, as a series.

    pct_change(periods=n) compares each row with the one n rows before it, which
    is R's (x / lag(x, n) - 1). Taking .iloc[-1] of the result gives today's
    figure; keeping the whole series lets the risk score be computed for every
    day in history rather than only today.
    """
    if len(series) <= trading_days:
        return None
    return series.pct_change(periods=trading_days) * 100


def rolling_percentile(series, window):
    """Where each value sits within the preceding `window` values, as 0-100.

    .rolling(window).rank(pct=True) ranks the newest value inside each window,
    so a reading of 90 means "higher than 90% of the past year". Ranking against
    a moving window rather than the whole history is what stops today's score
    being influenced by data that had not happened yet.
    """
    return series.rolling(window).rank(pct=True) * 100


def risk_band(score):
    """Turn a 0-100 score into (word, colour) using the RISK_BANDS table."""
    if score is None:
        return "no reading", COLOR_NORMAL
    label, color = RISK_BANDS[0][1], RISK_BANDS[0][2]
    for threshold, band_label, band_color in RISK_BANDS:
        if score >= threshold:
            label, color = band_label, band_color
    return label, color


def describe_vix(percentile):
    """Plain words for where volatility sits against its own past year."""
    if percentile is None:
        return "no reading", "flat"
    if percentile >= 90:
        return "stressed - volatility near its highest of the past year", "notable"
    if percentile >= 75:
        return "elevated", "mild"
    if percentile <= 25:
        return "calm", "flat"
    return "normal", "flat"


def fetch_risk_regime(reference_date):
    """Build the four indicators, their risk scores, and the combined watcher.

    Returns (tiles, curve_series, score_series_by_name, composite_series).

    Each indicator is turned into a 0-100 score where HIGHER ALWAYS MEANS MORE
    RISK, by taking its percentile rank over the trailing year and flipping the
    ones where a high raw reading is reassuring rather than worrying. Ranks are
    used instead of thresholds so that nothing depends on a number invented by
    the author: the data decides what counts as unusual for itself.
    """
    tiles = {}
    scores = {}
    curve_series = None

    # --- 1. VIX. Higher expected volatility means more risk, so no flip. -----
    vix = fetch_closes(VIX_TICKER, REGIME_HISTORY_PERIOD)
    if vix is not None:
        scores["Volatility"] = rolling_percentile(vix, RISK_SCORE_WINDOW)
        percentile = scores["Volatility"].iloc[-1]
        reading, _ = describe_vix(percentile)
        tiles["vix"] = {
            "label": "VIX (expected volatility)",
            "value": f"{vix.iloc[-1]:.1f}",
            "detail": f"{percentile:.0f}th percentile of the past year",
            "reading": reading,
            "score": percentile,
            "stale_days": days_behind(vix, reference_date),
        }

    # --- 2. Yield curve: 10-year minus 3-month, in basis points. -------------
    # Both are quoted in percent, so their difference is in percentage points;
    # multiplying by 100 turns it into basis points. A LOW or negative spread is
    # the worrying case, so the rank is flipped.
    ten_year = fetch_closes(YIELD_TICKER, REGIME_HISTORY_PERIOD)
    three_month = fetch_closes(BILL_TICKER, REGIME_HISTORY_PERIOD)
    if ten_year is not None and three_month is not None:
        combined = pd.concat(
            [ten_year.rename("ten"), three_month.rename("three")], axis=1, join="inner"
        ).dropna()
        if not combined.empty:
            curve_series = (combined["ten"] - combined["three"]) * 100
            scores["Yield curve"] = 100 - rolling_percentile(curve_series, RISK_SCORE_WINDOW)
            spread = curve_series.iloc[-1]
            inverted = spread < 0
            tiles["curve"] = {
                "label": "Yield curve (10y - 3m)",
                "value": f"{spread:+.0f}bp",
                "detail": f"10y {combined['ten'].iloc[-1]:.2f}% vs 3m {combined['three'].iloc[-1]:.2f}%",
                "reading": "inverted - the market expects rates to fall" if inverted
                           else "positively sloped, as normal",
                "score": scores["Yield curve"].iloc[-1],
                "stale_days": days_behind(curve_series, reference_date),
            }

    # --- 3. Credit: high-yield against investment-grade. ---------------------
    # High-yield lagging means lenders are pricing more defaults, so a HIGH
    # relative return is reassuring and the rank is flipped.
    high_yield = fetch_closes(HIGH_YIELD_TICKER, REGIME_HISTORY_PERIOD)
    investment_grade = fetch_closes(INVESTMENT_GRADE_TICKER, REGIME_HISTORY_PERIOD)
    if high_yield is not None and investment_grade is not None:
        combined = pd.concat(
            [high_yield.rename("hy"), investment_grade.rename("ig")], axis=1, join="inner"
        ).dropna()
        if not combined.empty:
            ratio = combined["hy"] / combined["ig"]
            move_series = change_over(ratio, REGIME_LOOKBACK_DAYS)
            if move_series is not None:
                scores["Credit"] = 100 - rolling_percentile(move_series, RISK_SCORE_WINDOW)
                move = move_series.iloc[-1]
                tiles["credit"] = {
                    "label": "Credit appetite (HYG vs LQD)",
                    "value": format_change(move),
                    "detail": "high-yield versus investment-grade, about three months",
                    "reading": "high-yield lagging - lenders turning cautious" if move < 0
                               else "high-yield holding up - credit relaxed",
                    "score": scores["Credit"].iloc[-1],
                    "stale_days": days_behind(ratio, reference_date),
                }

    # --- 4. The dollar. A rising dollar tightens conditions worldwide, ------
    # so a high recent gain counts as more risk and the rank is not flipped.
    dollar = fetch_closes(DOLLAR_TICKER, REGIME_HISTORY_PERIOD)
    if dollar is not None:
        move_series = change_over(dollar, REGIME_LOOKBACK_DAYS)
        if move_series is not None:
            scores["Dollar"] = rolling_percentile(move_series, RISK_SCORE_WINDOW)
            move = move_series.iloc[-1]
            tiles["dollar"] = {
                "label": "US dollar index",
                "value": f"{dollar.iloc[-1]:.1f}",
                "detail": f"{format_change(move)} over about three months",
                "reading": "strengthening - tighter conditions globally" if move > 0
                           else "softening - easier conditions globally",
                "score": scores["Dollar"].iloc[-1],
                "stale_days": days_behind(dollar, reference_date),
            }

    # --- The watcher: the average of whichever scores could be built. --------
    # axis=1 means "across the columns", so this averages the four indicators
    # for each date rather than averaging each indicator over time.
    composite = None
    if scores:
        aligned = pd.concat(scores.values(), axis=1, join="inner").dropna()
        if not aligned.empty:
            composite = aligned.mean(axis=1)

    return tiles, curve_series, scores, composite


# ---------------------------------------------------------------------------
# PORTFOLIO
# ---------------------------------------------------------------------------

REQUIRED_POSITION_FIELDS = ["name", "symbol", "quantity", "buy_price"]


def position_currency(symbol):
    """Which currency a Yahoo symbol is priced in.

    Borsa Istanbul symbols end in .IS and are quoted in lira; everything else
    here is quoted in dollars. Adding the two together without converting would
    produce a total that means nothing at all.
    """
    return "TRY" if symbol.upper().endswith(".IS") else "USD"


def parse_number(value):
    """Read a number written in either English or Turkish notation.

    A spreadsheet exports numbers the way its locale writes them. An English
    sheet gives 0.0125; a Turkish one gives 0,0125 and separates columns with
    semicolons instead of commas. Getting this wrong would not raise an error -
    it would silently value a holding at twelve thousand times its real price -
    so both are handled and the parsed figures are printed to the terminal for
    checking.

    A lone comma is treated as a decimal point, because that is the Turkish
    convention. Thousands separators are therefore not supported: write 50000,
    not 50.000 or 50,000.
    """
    if value is None:
        return None
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if not text:
        return None

    if "," in text and "." in text:
        # Whichever separator comes last is the decimal point.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def positions_from_csv(text):
    """Turn published-spreadsheet CSV into holdings, with readable complaints."""
    try:
        # sep=None asks pandas to work out the separator itself, which covers
        # both comma-separated and semicolon-separated exports.
        frame = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    except Exception as error:
        return [], [f"could not read the sheet as a table: {type(error).__name__}"]

    # Tolerate capitals, spaces and stray blanks in the header row.
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]

    required = ["symbol", "quantity", "buy_price"]   # buy_date is optional
    missing = [column for column in required if column not in frame.columns]
    if missing:
        return [], [
            f"the sheet is missing these columns: {', '.join(missing)}. "
            f"It needs a header row reading: name, symbol, quantity, "
            f"buy_price, type, where. Found instead: {', '.join(frame.columns)}"
        ]

    problems = []
    holdings = []
    for position, row in enumerate(frame.to_dict("records"), start=2):  # row 1 is the header
        symbol = str(row.get("symbol", "")).strip()
        if not symbol or symbol.lower() == "nan":
            continue  # blank rows at the bottom of a sheet are normal

        quantity = parse_number(row.get("quantity"))
        buy_price = parse_number(row.get("buy_price"))
        buy_date = str(row.get("buy_date", "")).strip()[:10]

        if quantity is None or buy_price is None:
            problems.append(f"row {position} ({symbol}): quantity or buy_price is not a number")
            continue

        # buy_date is optional. It is unrecoverable from a broker screenshot, and
        # it affects nothing except a "held since" label - not one profit or risk
        # figure. Requiring it would only invite an invented date.
        if buy_date and buy_date.lower() != "nan":
            try:
                date.fromisoformat(buy_date)
            except ValueError:
                problems.append(
                    f"row {position} ({symbol}): buy_date '{buy_date}' is not YYYY-MM-DD. "
                    f"Format that column as plain text in the sheet, or leave it empty."
                )
                buy_date = ""
        else:
            buy_date = ""

        name = str(row.get("name", "") or symbol).strip()
        holdings.append({
            "name": name if name.lower() != "nan" else symbol,
            "symbol": symbol,
            "quantity": quantity,
            "buy_price": buy_price,
            "buy_date": buy_date,
            "where": str(row.get("where", "") or "").replace("nan", ""),
            "kind": str(row.get("type", "") or "other").strip().lower().replace("nan", "other"),
            "currency": position_currency(symbol),
        })

    return holdings, problems


def load_positions_from_sheet(url):
    """Download the published sheet and parse it."""
    try:
        response = requests.get(url, timeout=FEED_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as error:
        return [], [
            f"could not download the holdings sheet: {str(error)[:120]}. "
            f"Check that it is still published (File > Share > Publish to web)."
        ]
    # Google serves the CSV as UTF-8; being explicit avoids mangled Turkish letters.
    response.encoding = "utf-8"
    return positions_from_csv(response.text)


def load_positions(path):
    """Read positions.json and return (settings, holdings, problems)."""
    if not path.exists():
        return {}, [], [f"positions.json was not found at {path}"]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {}, [], [
            f"positions.json is not valid JSON - line {error.lineno}, column "
            f"{error.colno}: {error.msg}. The usual causes are a missing comma "
            f"between two holdings, or a quote left unclosed."
        ]

    settings = {
        "is_example": bool(data.get("is_example", False)),
        "reporting_currency": str(data.get("reporting_currency", "USD")).upper(),
    }

    raw = data.get("positions")
    if not isinstance(raw, list):
        return settings, [], ["positions.json has no 'positions' list inside it."]

    problems = []
    holdings = []
    for position, entry in enumerate(raw, start=1):
        missing = [f for f in REQUIRED_POSITION_FIELDS if entry.get(f) in (None, "")]
        if missing:
            problems.append(f"holding {position} is missing: {', '.join(missing)}")
            continue
        try:
            quantity = float(entry["quantity"])
            buy_price = float(entry["buy_price"])
            if entry.get("buy_date"):
                date.fromisoformat(str(entry["buy_date"]))
        except (ValueError, TypeError):
            problems.append(
                f"holding {position} ('{entry.get('name')}') has a quantity, price "
                f"or date that is not a plain number or YYYY-MM-DD date."
            )
            continue
        holdings.append({
            "name": str(entry["name"]),
            "symbol": str(entry["symbol"]).strip(),
            "quantity": quantity,
            "buy_price": buy_price,
            "buy_date": str(entry.get("buy_date", "")),
            "kind": str(entry.get("type", "other")).lower(),
            "where": str(entry.get("where", "")),
            "currency": position_currency(str(entry["symbol"])),
        })

    return settings, holdings, problems


def build_portfolio(settings, holdings):
    """Price every holding, convert to one currency, and measure the whole.

    Returns (rows, totals, problems). A holding whose price cannot be fetched is
    reported and left out rather than guessed at.
    """
    problems = []
    reporting = settings.get("reporting_currency", "USD")

    # The live exchange rate, needed whenever lira and dollars appear together.
    fx = fetch_closes("USDTRY=X", "1y")
    usd_try = fx.iloc[-1] if fx is not None else None

    def to_reporting(amount, currency):
        """Convert an amount into the reporting currency, or None if we cannot."""
        if currency == reporting:
            return amount
        if usd_try is None:
            return None
        return amount * usd_try if currency == "USD" else amount / usd_try

    # The other currency, so every figure can be shown in both. Current values
    # convert cleanly at today's rate; the cost basis is already dollar-native,
    # so nothing here rests on a historical exchange rate.
    other = "TRY" if reporting == "USD" else "USD"

    def to_other(amount):
        if usd_try is None or amount is None:
            return None
        return amount * usd_try if reporting == "USD" else amount / usd_try

    rows = []
    returns_by_name = {}

    for holding in holdings:
        closes = fetch_closes(holding["symbol"], "1y")
        if closes is None:
            problems.append(f"no price data for {holding['symbol']} - left out of the totals")
            continue

        price = closes.iloc[-1]
        cost_native = holding["quantity"] * holding["buy_price"]
        value_native = holding["quantity"] * price

        cost = to_reporting(cost_native, holding["currency"])
        value = to_reporting(value_native, holding["currency"])
        if cost is None or value is None:
            problems.append(f"could not convert {holding['symbol']} into {reporting}")
            continue

        # Daily returns are kept so portfolio volatility can be measured properly
        # from how the holdings move together, not by averaging them.
        returns_by_name[holding["name"]] = closes.pct_change().dropna() * 100

        rows.append({
            **holding,
            "price": price,
            "cost": cost,
            "value": value,
            "profit": value - cost,
            "profit_pct": percent_change(value, cost) if cost else None,
            "value_other": to_other(value),
            "profit_other": to_other(value - cost),
            "as_of": closes.index[-1].date(),
        })

    if not rows:
        return [], {}, problems

    total_value = sum(row["value"] for row in rows)
    total_cost = sum(row["cost"] for row in rows)

    for row in rows:
        row["weight"] = row["value"] / total_value * 100 if total_value else 0

    rows.sort(key=lambda row: row["value"], reverse=True)

    totals = {
        "value": total_value,
        "cost": total_cost,
        "profit": total_value - total_cost,
        "profit_pct": percent_change(total_value, total_cost) if total_cost else None,
        "currency": reporting,
        "usd_try": usd_try,
        "other_currency": other,
        "value_other": to_other(total_value),
        "profit_other": to_other(total_value - total_cost),
        "top_three": sum(row["weight"] for row in rows[:3]),
        "count": len(rows),
    }
    totals.update(measure_portfolio_risk(rows, returns_by_name))
    return rows, totals, problems


def measure_portfolio_risk(rows, returns_by_name):
    """Portfolio volatility, the diversification it buys, and correlations.

    THE POINT OF THIS FUNCTION. Portfolio risk is not the average of the risks of
    what you hold. Two holdings that rise and fall together are nearly one
    holding; two that move independently partly cancel out. The proper
    calculation combines the weights with the covariance matrix - how each pair
    moves together - and the gap between that answer and the naive weighted
    average is the diversification you are actually getting.
    """
    if len(returns_by_name) < 2:
        return {}

    # Line the daily returns up by date. Crypto trades at weekends and shares do
    # not, so join="inner" keeps only the days when everything traded.
    aligned = pd.concat(returns_by_name.values(), axis=1, join="inner").dropna()
    aligned.columns = list(returns_by_name.keys())
    if len(aligned) < 30:
        return {}

    weights = pd.Series(
        {row["name"]: row["weight"] / 100 for row in rows if row["name"] in aligned.columns}
    )
    weights = weights.reindex(aligned.columns).fillna(0)
    if weights.sum() == 0:
        return {}
    weights = weights / weights.sum()

    # Individual annualised volatilities.
    individual = aligned.std() * sqrt(TRADING_DAYS_PER_YEAR)

    # The naive figure: what portfolio volatility would be if everything moved
    # in perfect lockstep.
    weighted_average = float((weights * individual).sum())

    # The real figure: w' C w, where C is the covariance matrix. The square root
    # turns variance back into volatility.
    covariance = aligned.cov()
    variance = float(weights.values @ covariance.values @ weights.values)
    portfolio_vol = sqrt(max(variance, 0)) * sqrt(TRADING_DAYS_PER_YEAR)

    return {
        "portfolio_vol": portfolio_vol,
        "weighted_avg_vol": weighted_average,
        "diversification": weighted_average - portfolio_vol,
        "correlations": aligned.corr(),
        "overlap_days": len(aligned),
    }


# ---------------------------------------------------------------------------
# NEWS AND CALENDAR
# ---------------------------------------------------------------------------

def fetch_feed(name, url, max_age_days):
    """Download one RSS feed and return its recent entries.

    Two details worth understanding.

    The download goes through `requests` rather than letting feedparser fetch the
    address itself. Python installed from python.org does not use macOS's
    certificate store, so its built-in downloader rejects every https feed with a
    certificate error; requests carries its own certificates and simply works. It
    also lets us set a timeout, so one unresponsive feed cannot hang the whole
    scheduled run at six in the morning.

    We keep the headline, source, date and link, and nothing else. The article
    text belongs to the publisher; an RSS feed is an invitation to link to a
    story, not to reproduce it.
    """
    try:
        response = requests.get(
            url,
            timeout=FEED_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (daily-market-brief)"},
        )
        response.raise_for_status()   # turns a 404 or 500 into an error we catch
    except requests.RequestException as error:
        # One dead feed must never take the rest of the page down with it.
        return {"source": name, "items": [], "error": str(error)[:140]}

    parsed = feedparser.parse(response.content)
    cutoff = datetime.now() - timedelta(days=max_age_days)
    items = []

    for entry in parsed.entries:
        published = None
        if getattr(entry, "published_parsed", None):
            # published_parsed is a nine-part time tuple; the first six parts are
            # year, month, day, hour, minute, second.
            published = datetime(*entry.published_parsed[:6])
            if published < cutoff:
                continue

        link = entry.get("link", "")
        # Only ordinary web links get through. A feed is data from outside this
        # project, and outside data does not get to decide what the page links to.
        if not link.startswith(("http://", "https://")):
            continue

        items.append({
            "title": entry.get("title", "").strip(),
            "link": link,
            "published": published,
        })
        if len(items) >= HEADLINES_PER_FEED:
            break

    return {"source": name, "items": items, "error": None}


def fetch_headlines():
    """Fetch every configured feed."""
    return [fetch_feed(name, url, max_age) for name, url, max_age in FEEDS]


# The fields every calendar entry must have before the page will show it.
REQUIRED_EVENT_FIELDS = ["date", "name", "why", "if_higher", "if_lower"]


def load_calendar(path):
    """Read calendar.json and return (upcoming events, problems to report).

    Every likely hand-editing mistake gets its own plain-English message instead
    of a Python traceback, because this is the one file you maintain and the
    error needs to tell you what to fix.
    """
    if not path.exists():
        return [], [f"calendar.json was not found at {path}"]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [], [
            f"calendar.json is not valid JSON - line {error.lineno}, column "
            f"{error.colno}: {error.msg}. The usual causes are a missing comma "
            f"between two blocks, or a quote left unclosed."
        ]

    events = data.get("events")
    if not isinstance(events, list):
        return [], ["calendar.json has no 'events' list inside it."]

    today = datetime.now(TIMEZONE).date()
    problems = []
    upcoming = []

    # enumerate(..., start=1) numbers the events from 1, so the message below
    # matches what a person counting blocks in the file would say.
    for position, event in enumerate(events, start=1):
        missing = [field for field in REQUIRED_EVENT_FIELDS if not event.get(field)]
        if missing:
            problems.append(f"event {position} is missing: {', '.join(missing)}")
            continue

        try:
            event_date = date.fromisoformat(event["date"])
        except (ValueError, TypeError):
            problems.append(
                f"event {position} ('{event['name']}') has date '{event['date']}', "
                f"which is not in YYYY-MM-DD form."
            )
            continue

        if event_date < today:
            continue  # past events drop off the page by themselves

        # {**event, "extra": value} copies a dict and adds a key, leaving the
        # original untouched - like modifyList() in R.
        upcoming.append({**event, "parsed_date": event_date})

    upcoming.sort(key=lambda event: event["parsed_date"])

    if not upcoming:
        problems.append(
            "no upcoming events left in calendar.json - time to add the next month."
        )

    return upcoming[:CALENDAR_EVENTS_SHOWN], problems


# ---------------------------------------------------------------------------
# FORMATTING
# ---------------------------------------------------------------------------

def format_change(change):
    """Percentage as text, e.g. '+1.24%'. 'n/a' when we have no value."""
    if change is None:
        return "n/a"
    return f"{change:+.2f}%"


def format_basis_points(change):
    """Yield move as text, e.g. '+6.0bp'."""
    if change is None:
        return "n/a"
    return f"{change:+.1f}bp"


def format_number(value, places=2, suffix=""):
    """A plain number, or 'n/a' when it is missing.

    Rounding a tiny negative number gives "-0.0", which looks like a mistake.
    Snapping it to a clean zero avoids that.
    """
    if value is None:
        return "n/a"
    if round(value, places) == 0:
        value = 0.0
    return f"{value:.{places}f}{suffix}"


def format_signed(value, places=2):
    """A number that always carries its sign, e.g. '-0.42'."""
    if value is None:
        return "n/a"
    return f"{value:+.{places}f}"


def change_class(change):
    """CSS class name so the page can colour a number by its sign."""
    if change is None:
        return "flat"
    return "up" if change >= 0 else "down"


def unusualness_label(z_score):
    """Plain-language reading of a z-score. Wording only - no numbers invented."""
    if z_score is None:
        return "n/a", "flat"
    size = abs(z_score)
    if size >= 2:
        return "unusual", "notable"
    if size >= 1:
        return "above average", "mild"
    return "ordinary", "flat"


# ---------------------------------------------------------------------------
# CHARTS
# ---------------------------------------------------------------------------

BASE_LAYOUT = dict(
    paper_bgcolor=COLOR_SURFACE,
    plot_bgcolor=COLOR_SURFACE,
    font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif",
              size=13, color=COLOR_INK),
    margin=dict(l=8, r=8, t=8, b=8),
)


def to_html_fragment(figure):
    """Turn a chart into a chunk of HTML to paste into the page.

    No chart carries the library: the page loads it once in <head> instead. An
    earlier version attached it to whichever chart came first, which broke the
    moment the cards were reordered - a chart above the library tried to draw
    before the library existed. Loading it in <head> makes order irrelevant.
    """
    return figure.to_html(
        full_html=False,
        include_plotlyjs=False,
        config={"displayModeBar": False, "responsive": True},
    )


def build_bar_chart(rows):
    """Horizontal bars of each ETF's move, sorted, coloured by direction."""
    usable = [row for row in rows if row[BAR_COLUMN] is not None]
    usable.sort(key=lambda row: row[BAR_COLUMN])

    if not usable:
        return "<p class='missing'>No price data available for this chart.</p>"

    values = [row[BAR_COLUMN] for row in usable]
    names = [row["ticker"] for row in usable]

    figure = go.Figure(
        go.Bar(
            x=values, y=names, orientation="h",
            marker=dict(color=[COLOR_UP if v >= 0 else COLOR_DOWN for v in values],
                        cornerradius=4),
            # Every bar is labelled with its own value, so the chart is readable
            # without hovering - a tooltip should add detail, never be the only
            # way to read a number.
            text=[format_change(v) for v in values],
            textposition="outside",
            textfont=dict(color=COLOR_INK),
            hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
        )
    )

    span = max(abs(min(values)), abs(max(values))) * 1.35 or 1
    figure.update_layout(
        **BASE_LAYOUT, height=300, bargap=0.45,
        xaxis=dict(range=[-span, span], zeroline=True, zerolinecolor=COLOR_MUTED,
                   zerolinewidth=1, gridcolor=COLOR_GRID, griddash="solid",
                   ticksuffix="%", tickfont=dict(color=COLOR_MUTED)),
        yaxis=dict(showgrid=False, tickfont=dict(color=COLOR_INK, size=14)),
    )
    return to_html_fragment(figure)


def build_trend_chart(series_by_ticker):
    """Two rebased price lines on one shared axis."""
    if not series_by_ticker:
        return "<p class='missing'>No price data available for this chart.</p>"

    figure = go.Figure()
    colors = [COLOR_UP, COLOR_SERIES_2]

    # enumerate() gives position and item together, like seq_along() in R.
    for position, (ticker, series) in enumerate(series_by_ticker.items()):
        color = colors[position % len(colors)]
        figure.add_trace(
            go.Scatter(x=series.index, y=series.values, name=ticker, mode="lines",
                       line=dict(color=color, width=2),
                       hovertemplate="%{y:.1f}<extra>" + ticker + "</extra>")
        )
        # Label the end of each line directly, so identity never rests on
        # colour alone for a reader who cannot separate the two hues.
        figure.add_annotation(x=series.index[-1], y=series.values[-1], text=f"  {ticker}",
                              showarrow=False, xanchor="left",
                              font=dict(color=color, size=13))

    figure.update_layout(
        **{**BASE_LAYOUT, "margin": dict(l=8, r=54, t=8, b=8)},
        height=340, hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0, font=dict(color=COLOR_INK)),
        xaxis=dict(showgrid=False, tickfont=dict(color=COLOR_MUTED), linecolor=COLOR_GRID),
        yaxis=dict(gridcolor=COLOR_GRID, griddash="solid", tickfont=dict(color=COLOR_MUTED)),
    )
    return to_html_fragment(figure)


def build_correlation_chart(correlation):
    """The rolling correlation over time, with zero marked."""
    if correlation is None:
        return "<p class='missing'>Not enough overlapping data to compute a correlation.</p>"

    figure = go.Figure(
        go.Scatter(x=correlation.index, y=correlation.values, mode="lines",
                   line=dict(color=COLOR_UP, width=2),
                   hovertemplate="%{y:.2f}<extra></extra>")
    )
    # Zero is the meaningful reference: above it and below it mean opposite things.
    figure.add_hline(y=0, line_color=COLOR_MUTED, line_width=1)

    figure.update_layout(
        **BASE_LAYOUT, height=280, hovermode="x unified", showlegend=False,
        xaxis=dict(showgrid=False, tickfont=dict(color=COLOR_MUTED), linecolor=COLOR_GRID),
        yaxis=dict(range=[-1, 1], gridcolor=COLOR_GRID, griddash="solid",
                   tickfont=dict(color=COLOR_MUTED)),
    )
    return to_html_fragment(figure)


# ---------------------------------------------------------------------------
# PAGE
# ---------------------------------------------------------------------------

# Kept as a plain (non-f) string because CSS is full of { } braces, which would
# confuse an f-string. The values get slotted in further down instead.
STYLES = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 64px;
  background: #f9f9f7; color: #0b0b0b;
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
  line-height: 1.55;
}
.wrap { max-width: 940px; margin: 0 auto; }
header { margin-bottom: 28px; }
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.01em; }
.stamp { color: #52514e; font-size: 14px; margin: 0; }
.card {
  background: #fcfcfb; border: 1px solid rgba(11,11,11,0.10);
  border-radius: 12px; padding: 20px 22px; margin-bottom: 20px;
}
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 0.06em;
     color: #52514e; margin: 0 0 16px; font-weight: 600; }
.note {
  margin: 16px 0 0; padding-top: 14px; border-top: 1px solid #e1e0d9;
  color: #52514e; font-size: 14px;
}
.note b { color: #0b0b0b; font-weight: 600; }
.hero { font-size: 42px; font-weight: 600; letter-spacing: -0.02em; }
.hero-row { display: flex; align-items: baseline; gap: 18px; flex-wrap: wrap; }
.hero-side { color: #52514e; font-size: 14px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 9px 8px; text-align: right; border-bottom: 1px solid #e1e0d9; }
th { color: #898781; font-size: 12px; font-weight: 600; text-transform: uppercase;
     letter-spacing: 0.04em; }
th:first-child, td:first-child { text-align: left; }
tbody tr:last-child td { border-bottom: none; }
.tk { font-weight: 600; }
.up { color: #185ea8; }
.down { color: #c0332f; }
.flat, .missing { color: #898781; }
.tag { font-size: 12px; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.tag.flat { background: #f0efec; color: #52514e; }
.tag.mild { background: #fdf1e4; color: #8a4b1d; }
.tag.notable { background: #fbe6e6; color: #a32b2b; }
.table-scroll, .chart-scroll { overflow-x: auto; }
footer { color: #898781; font-size: 13px; margin-top: 28px; }

/* Risk watcher */
.watch-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.watch-score { font-size: 52px; font-weight: 600; letter-spacing: -0.03em; line-height: 1; }
.watch-label { font-size: 22px; font-weight: 600; }
.watch-of { color: #898781; font-size: 15px; }
.watch-drift { color: #52514e; font-size: 14px; margin-top: 6px; }
.gauge { display: flex; height: 10px; border-radius: 999px; overflow: hidden;
         margin: 18px 0 6px; position: relative; }
.zone { height: 100%; opacity: 0.30; }
.marker { position: absolute; top: -4px; width: 3px; height: 18px; background: #0b0b0b;
          border-radius: 2px; transform: translateX(-1px); }
.gauge-scale { display: flex; justify-content: space-between; color: #898781; font-size: 12px; }
.comps { margin-top: 18px; display: grid; gap: 8px; }
.comp { display: grid; grid-template-columns: 110px 1fr 34px 92px; gap: 10px;
        align-items: center; font-size: 13px; }
.comp-name { color: #52514e; }
.comp-track { background: #f0efec; border-radius: 999px; height: 8px; overflow: hidden; }
.comp-fill { height: 100%; border-radius: 999px; }
.comp-score { text-align: right; font-variant-numeric: tabular-nums; color: #52514e; }
.comp-word { font-weight: 600; }
@media (max-width: 620px) {
  .comp { grid-template-columns: 92px 1fr 30px; }
  .comp-word { display: none; }
}

/* Regime tiles */
.tile-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.tile { border: 1px solid #e1e0d9; border-radius: 10px; padding: 14px 16px; }
.tile-label { font-size: 12px; color: #898781; text-transform: uppercase;
              letter-spacing: 0.04em; font-weight: 600; }
.tile-value { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; margin: 6px 0 2px; }
.tile-detail { font-size: 13px; color: #52514e; margin-bottom: 8px; }
/* These readings are sentences, not one-word labels, so they must be allowed to
   wrap - otherwise nowrap pushes them straight out of the tile. */
.tile-reading { font-size: 13px; }
.tile-reading .reading { font-weight: 600; line-height: 1.4; }

/* TradingView widgets */
.tv-widget { min-height: 480px; }
.tv-widget iframe { border-radius: 8px; }

/* Portfolio */
.alt { color: #52514e; }
.awaiting { color: #52514e; font-size: 14px; background: #f9f9f7; border: 1px dashed #c3c2b7;
            border-radius: 8px; padding: 16px 18px; margin: 0; }
.sym { color: #898781; font-size: 13px; }
.sub { font-size: 14px; margin: 24px 0 10px; color: #52514e; font-weight: 600; }
.corr td { text-align: center; font-variant-numeric: tabular-nums; font-size: 13px; }
.corr th { text-align: center; }
.fine { color: #898781; font-size: 13px; margin: 8px 0 0; }

/* News */
.feed-grid { display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); }
.feed h3 { font-size: 13px; margin: 0 0 10px; color: #185ea8; font-weight: 600;
           letter-spacing: 0.02em; }
.heads { list-style: none; margin: 0; padding: 0; }
.heads li { padding: 8px 0; border-bottom: 1px solid #e1e0d9; font-size: 14px; }
.heads li:last-child { border-bottom: none; }
.heads a { color: #0b0b0b; text-decoration: none; }
.heads a:hover { text-decoration: underline; }
.when { display: block; color: #898781; font-size: 12px; margin-top: 3px; }

/* Calendar */
.event { padding: 16px 0; border-bottom: 1px solid #e1e0d9; }
.event:last-child { border-bottom: none; }
.event-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.event-date { font-weight: 600; font-variant-numeric: tabular-nums; }
.event-when { color: #898781; font-size: 13px; }
.event-name { font-weight: 600; margin: 4px 0 6px; }
.event-why { color: #52514e; font-size: 14px; margin: 0 0 10px; }
.imp { font-size: 14px; margin: 0 0 6px; padding-left: 12px; border-left: 2px solid #e1e0d9; }
.imp b { font-weight: 600; }
.imp.hi { border-left-color: #e34948; }
.imp.lo { border-left-color: #2a78d6; }
.unconfirmed { font-size: 12px; padding: 2px 8px; border-radius: 999px;
               background: #fdf1e4; color: #8a4b1d; text-decoration: none;
               white-space: nowrap; }
.unconfirmed:hover { text-decoration: underline; }
.warn { background: #fdf1e4; border: 1px solid #f0d9bd; color: #8a4b1d;
        border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; font-size: 14px; }
.warn ul { margin: 6px 0 0; padding-left: 20px; }
"""


def build_table(rows):
    """The plain price table."""
    lines = []
    for row in rows:
        if row is None:
            continue
        lines.append(
            "<tr>"
            f"<td class='tk'>{row['ticker']}</td>"
            f"<td>{row['close']:.2f}</td>"
            f"<td class='{change_class(row['change_1d'])}'>{format_change(row['change_1d'])}</td>"
            f"<td class='{change_class(row['change_1w'])}'>{format_change(row['change_1w'])}</td>"
            f"<td class='{change_class(row['change_ytd'])}'>{format_change(row['change_ytd'])}</td>"
            "</tr>"
        )
    # "\n".join(list) glues strings together, like paste(collapse="\n").
    return "\n".join(lines)


def build_risk_table(risk_rows):
    """The statistics table: z-score, volatility and its rank, drawdown."""
    lines = []
    for risk in risk_rows:
        if risk is None:
            continue
        label, tone = unusualness_label(risk["z_score"])
        lines.append(
            "<tr>"
            f"<td class='tk'>{risk['ticker']}</td>"
            f"<td class='{change_class(risk['change_1d'])}'>{format_change(risk['change_1d'])}</td>"
            f"<td>{format_signed(risk['z_score'])}</td>"
            f"<td><span class='tag {tone}'>{label}</span></td>"
            f"<td>{format_number(risk['volatility'], 1, '%')}</td>"
            f"<td>{format_number(risk['volatility_percentile'], 0, 'th')}</td>"
            # A fund sitting at its high has no drawdown to report in red.
            f"<td class='{'flat' if risk['drawdown'] > -0.05 else 'down'}'>"
            f"{format_number(risk['drawdown'], 1, '%')}</td>"
            "</tr>"
        )
    if not lines:
        return "<tr><td class='missing'>No data available.</td></tr>"
    return "\n".join(lines)


def build_watcher_html(composite, scores):
    """The combined risk reading: one number, its word, and what produced it."""
    if composite is None or composite.empty:
        return "<p class='missing'>Not enough data to build a combined reading.</p>"

    score = composite.iloc[-1]
    label, color = risk_band(score)

    # A month ago, for direction of travel. One number tells you where you are;
    # two tell you which way things are heading, which is usually the question.
    previous = composite.iloc[-22] if len(composite) > 22 else None
    if previous is None:
        drift = ""
    else:
        move = score - previous
        word = "rising" if move > 3 else ("falling" if move < -3 else "steady")
        drift = f"<div class='watch-drift'>{word} &mdash; {move:+.0f} points over the past month</div>"

    # The coloured zones behind the marker, built from the same RISK_BANDS table
    # that decides the wording, so the two can never disagree.
    zones = []
    for position, (start, band_label, band_color) in enumerate(RISK_BANDS):
        end = RISK_BANDS[position + 1][0] if position + 1 < len(RISK_BANDS) else 100
        zones.append(
            f"<div class='zone' style='width:{end - start}%;background:{band_color}' "
            f"title='{band_label}'></div>"
        )

    rows = []
    for name, series in scores.items():
        value = series.iloc[-1]
        row_label, row_color = risk_band(value)
        rows.append(
            "<div class='comp'>"
            f"<div class='comp-name'>{escape(name)}</div>"
            "<div class='comp-track'>"
            f"<div class='comp-fill' style='width:{max(value, 2):.0f}%;background:{row_color}'></div>"
            "</div>"
            f"<div class='comp-score'>{value:.0f}</div>"
            f"<div class='comp-word' style='color:{row_color}'>{row_label}</div>"
            "</div>"
        )

    return (
        "<div class='watch'>"
        f"<div class='watch-head'>"
        f"<span class='watch-score' style='color:{color}'>{score:.0f}</span>"
        f"<span class='watch-label' style='color:{color}'>{label}</span>"
        f"<span class='watch-of'>/ 100</span>"
        f"</div>{drift}"
        f"<div class='gauge'>{''.join(zones)}"
        f"<div class='marker' style='left:{score:.1f}%'></div></div>"
        "<div class='gauge-scale'><span>0 calm</span><span>100 stressed</span></div>"
        f"<div class='comps'>{''.join(rows)}</div>"
        "</div>"
    )


def build_watcher_chart(composite):
    """The combined score over the past year, on its coloured bands."""
    if composite is None or composite.empty:
        return "<p class='missing'>Not enough history to chart the risk score.</p>"

    figure = go.Figure()

    # Shade each band across the whole width, so the line's height reads as a
    # state rather than as a bare number.
    for position, (start, band_label, band_color) in enumerate(RISK_BANDS):
        end = RISK_BANDS[position + 1][0] if position + 1 < len(RISK_BANDS) else 100
        figure.add_hrect(y0=start, y1=end, fillcolor=band_color, opacity=0.10,
                         line_width=0, annotation_text=band_label,
                         annotation_position="top left",
                         annotation_font=dict(color=COLOR_MUTED, size=11))

    figure.add_trace(
        go.Scatter(x=composite.index, y=composite.values, mode="lines",
                   line=dict(color=COLOR_INK, width=2),
                   hovertemplate="%{y:.0f}<extra></extra>")
    )
    figure.update_layout(
        **BASE_LAYOUT, height=280, hovermode="x unified", showlegend=False,
        xaxis=dict(showgrid=False, tickfont=dict(color=COLOR_MUTED), linecolor=COLOR_GRID),
        yaxis=dict(range=[0, 100], gridcolor=COLOR_GRID, griddash="solid",
                   tickfont=dict(color=COLOR_MUTED)),
    )
    return to_html_fragment(figure)


def build_regime_html(regime):
    """The four regime tiles, each flagged if its data has gone stale."""
    if not regime:
        return "<p class='missing'>No regime indicators could be loaded.</p>"

    tiles = []
    for item in regime.values():
        # A stale indicator is labelled rather than quietly presented as current.
        stale = ""
        if item["stale_days"] > STALE_AFTER_DAYS:
            stale = (f"<span class='unconfirmed'>{item['stale_days']} days old - "
                     f"source stopped updating</span>")

        # The tile is coloured by its own risk score, using the same bands as the
        # watcher above it. The colour is a stripe and a word, never the only
        # signal: the reading is always spelled out beside it.
        _, color = risk_band(item["score"])

        tiles.append(
            f"<div class='tile' style='border-top:3px solid {color}'>"
            f"<div class='tile-label'>{escape(item['label'])}</div>"
            f"<div class='tile-value'>{escape(item['value'])}</div>"
            f"<div class='tile-detail'>{escape(item['detail'])}</div>"
            f"<div class='tile-reading'><span class='reading' "
            f"style='color:{color}'>{escape(item['reading'])}</span></div>"
            f"{stale}"
            "</div>"
        )

    return "<div class='tile-grid'>" + "".join(tiles) + "</div>"


def build_curve_chart(curve_series):
    """The 10-year minus 3-month spread over the past year, with zero marked."""
    if curve_series is None or curve_series.empty:
        return "<p class='missing'>Not enough data to draw the yield curve.</p>"

    # Colour by side of the line: below zero is the condition worth noticing.
    figure = go.Figure(
        go.Scatter(x=curve_series.index, y=curve_series.values, mode="lines",
                   line=dict(color=COLOR_UP, width=2),
                   hovertemplate="%{y:+.0f}bp<extra></extra>")
    )
    figure.add_hline(y=0, line_color=COLOR_DOWN, line_width=1)

    figure.update_layout(
        **BASE_LAYOUT, height=260, hovermode="x unified", showlegend=False,
        xaxis=dict(showgrid=False, tickfont=dict(color=COLOR_MUTED), linecolor=COLOR_GRID),
        yaxis=dict(gridcolor=COLOR_GRID, griddash="solid", ticksuffix="bp",
                   tickfont=dict(color=COLOR_MUTED)),
    )
    return to_html_fragment(figure)


def money(amount, currency):
    """Format an amount of money, or 'n/a' when it is missing."""
    if amount is None:
        return "n/a"
    symbol = "$" if currency == "USD" else "₺"
    return f"{symbol}{amount:,.2f}"


def build_portfolio_html(settings, rows, totals, problems):
    """The holdings table, the totals, and what the portfolio's risk really is."""
    blocks = []

    if settings.get("is_example"):
        blocks.append(
            "<div class='warn'><b>These are placeholder numbers.</b> The symbols come "
            "from your TradingView list, but the quantities, buy dates and buy prices "
            "are invented so the panel has something to show. Edit positions.json with "
            "your real figures and set is_example to false.</div>"
        )

    if problems:
        listed = "".join(f"<li>{escape(problem)}</li>" for problem in problems)
        blocks.append(f"<div class='warn'><b>positions.json needs attention</b>"
                      f"<ul>{listed}</ul></div>")

    if not rows:
        blocks.append(
            "<p class='awaiting'><b>No holdings recorded yet.</b> A watchlist says what "
            "you are watching; it does not say how much you own or what you paid, and "
            "neither of those can be inferred from a price. Add quantity, purchase date "
            "and purchase price to positions.json and every figure here - value, profit, "
            "weights, portfolio volatility, correlations - will be computed from them.</p>"
        )
        return "".join(blocks)

    currency = totals["currency"]

    # Headline: what it is worth, and what that cost.
    blocks.append(
        "<div class='hero-row'>"
        f"<span class='hero'>{money(totals['value'], currency)}</span>"
        f"<span class='hero-side'>"
        f"<b class='{change_class(totals['profit'])}'>"
        f"{'+' if totals['profit'] >= 0 else '-'}{money(abs(totals['profit']), currency)} "
        f"({format_change(totals['profit_pct'])})</b> since purchase<br>"
        f"= {money(totals['value_other'], totals['other_currency'])} &nbsp;·&nbsp; "
        f"{'+' if totals['profit'] >= 0 else '-'}"
        f"{money(abs(totals['profit_other']), totals['other_currency'])}<br>"
        f"{totals['count']} holdings &nbsp;·&nbsp; top three are "
        f"{totals['top_three']:.0f}% of the total"
        + (f" &nbsp;·&nbsp; USD/TRY {totals['usd_try']:.2f}" if totals.get("usd_try") else "")
        + "</span></div>"
    )

    # The holdings themselves.
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td class='tk'>{escape(row['name'])}</td>"
            f"<td class='sym'>{escape(row['symbol'])}</td>"
            f"<td>{row['quantity']:,.4f}".rstrip("0").rstrip(".") + "</td>"
            f"<td>{row['buy_price']:,.4f}".rstrip("0").rstrip(".") + "</td>"
            f"<td>{row['price']:,.4f}".rstrip("0").rstrip(".") + "</td>"
            f"<td>{money(row['value'], currency)}</td>"
            f"<td class='alt'>{money(row['value_other'], totals['other_currency'])}</td>"
            f"<td class='{change_class(row['profit'])}'>"
            f"{'+' if row['profit'] >= 0 else '-'}{money(abs(row['profit']), currency)}</td>"
            f"<td class='{change_class(row['profit_pct'])}'>{format_change(row['profit_pct'])}</td>"
            f"<td>{row['weight']:.1f}%</td>"
            "</tr>"
        )

    blocks.append(
        "<div class='table-scroll'><table><thead><tr>"
        "<th>Holding</th><th>Symbol</th><th>Qty</th><th>Bought at</th><th>Now</th>"
        f"<th>Value ({'$' if currency == 'USD' else '₺'})</th>"
        f"<th>Value ({'₺' if currency == 'USD' else '$'})</th>"
        f"<th>Profit</th><th>%</th><th>Weight</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )

    # Portfolio risk: the part no broker shows you.
    if totals.get("portfolio_vol") is not None:
        saved = totals["diversification"]
        blocks.append(
            "<div class='tile-grid' style='margin-top:20px'>"
            "<div class='tile'>"
            "<div class='tile-label'>Portfolio volatility</div>"
            f"<div class='tile-value'>{totals['portfolio_vol']:.1f}%</div>"
            "<div class='tile-detail'>annualised, from how your holdings actually "
            "move together</div></div>"
            "<div class='tile'>"
            "<div class='tile-label'>If they moved as one</div>"
            f"<div class='tile-value'>{totals['weighted_avg_vol']:.1f}%</div>"
            "<div class='tile-detail'>the weighted average of each holding's own "
            "volatility</div></div>"
            "<div class='tile'>"
            "<div class='tile-label'>Diversification benefit</div>"
            f"<div class='tile-value' style='color:{COLOR_CALM}'>{saved:.1f}pp</div>"
            "<div class='tile-detail'>volatility removed by not moving in "
            "lockstep</div></div>"
            "</div>"
        )

    # The correlation grid: which holdings are genuinely different bets.
    correlations = totals.get("correlations")
    if correlations is not None and len(correlations) >= 2:
        names = list(correlations.columns)
        header = "".join(f"<th>{escape(n[:9])}</th>" for n in names)
        grid = []
        for row_name in names:
            cells = []
            for col_name in names:
                value = correlations.loc[row_name, col_name]
                # Tint by strength: darker means the pair moves together more.
                shade = min(abs(value), 1.0)
                background = (f"rgba(42,120,214,{shade * 0.55:.2f})" if value >= 0
                              else f"rgba(227,73,72,{shade * 0.55:.2f})")
                cells.append(f"<td style='background:{background}'>{value:.2f}</td>")
            grid.append(f"<tr><td class='tk'>{escape(row_name[:14])}</td>{''.join(cells)}</tr>")

        blocks.append(
            "<h3 class='sub'>How your holdings move together</h3>"
            "<div class='table-scroll'><table class='corr'><thead><tr><th></th>"
            + header + "</tr></thead><tbody>" + "".join(grid) + "</tbody></table></div>"
            f"<p class='fine'>Measured over the {totals['overlap_days']} days when every "
            "holding traded. 1.00 means they move identically; 0 means they are "
            "unrelated; below zero means they tend to move in opposite directions.</p>"
        )

    return "".join(blocks)


def to_tradingview_symbol(symbol, kind):
    """Turn a Yahoo symbol into the one TradingView expects."""
    if symbol in TRADINGVIEW_SYMBOLS:
        return TRADINGVIEW_SYMBOLS[symbol]
    if symbol.endswith(".IS"):
        return f"BIST:{symbol[:-3]}"
    if symbol.endswith("-USD"):
        # You buy these on OKX, so its own pair is the honest chart to show.
        return f"OKX:{symbol[:-4]}USDT"
    return symbol


def build_tradingview_chart(rows):
    """A live TradingView chart, with your holdings as its watchlist.

    Unlike everything else on this page, this is not computed here. It is
    TradingView's own JavaScript, fetching its own live data in your browser
    when you open the page. That is the point - it shows intraday movement a
    once-every-three-hours static page cannot - but it also means this panel is
    the one part that depends on somebody else's service being up.
    """
    watchlist = [to_tradingview_symbol(row["symbol"], row["kind"])
                 for row in rows if row["kind"] != "cash"]
    if not watchlist:
        return "<p class='missing'>No holdings to chart.</p>"

    config = {
        "symbol": watchlist[0],
        "watchlist": watchlist,
        "interval": "D",
        "timezone": "Europe/Istanbul",
        "theme": "light",
        "style": "1",
        "locale": "en",
        "hide_side_toolbar": False,
        "allow_symbol_change": True,
        "details": True,
        "width": "100%",
        "height": 520,
    }
    return (
        "<div class='tv-widget'><div class='tradingview-widget-container'>"
        "<div class='tradingview-widget-container__widget'></div>"
        "<script type='text/javascript' "
        "src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js' "
        f"async>{json.dumps(config)}</script>"
        "</div></div>"
    )


def build_tradingview_calendar():
    """TradingView's economic calendar - the authoritative dates."""
    config = {
        "colorTheme": "light",
        "isTransparent": False,
        "locale": "en",
        "countryFilter": "us,eu,tr",
        "importanceFilter": "0,1",
        "width": "100%",
        "height": 480,
    }
    return (
        "<div class='tv-widget'><div class='tradingview-widget-container'>"
        "<div class='tradingview-widget-container__widget'></div>"
        "<script type='text/javascript' "
        "src='https://s3.tradingview.com/external-embedding/embed-widget-events.js' "
        f"async>{json.dumps(config)}</script>"
        "</div></div>"
    )


def build_headlines_html(feeds):
    """One card per source, each a list of headlines that link out.

    Note escape() on every piece of feed text. Headlines are written by someone
    else and arrive over the internet; a title containing a stray angle bracket
    would otherwise break the page, and a deliberately crafted one could inject
    markup into it. escape() turns those characters into harmless text. Treat
    anything fetched from outside as data to display, never as code to run.
    """
    cards = []
    for feed in feeds:
        if feed["error"]:
            body = f"<p class='missing'>Could not load this feed: {escape(feed['error'])}</p>"
        elif not feed["items"]:
            body = "<p class='missing'>No headlines in the last few days.</p>"
        else:
            rows = []
            for item in feed["items"]:
                when = item["published"].strftime("%d %b") if item["published"] else ""
                rows.append(
                    "<li>"
                    f"<a href='{escape(item['link'], quote=True)}' target='_blank' "
                    f"rel='noopener noreferrer'>{escape(item['title'])}</a>"
                    # The card heading already names the source, so each row
                    # only needs its date.
                    f"<span class='when'>{when}</span>"
                    "</li>"
                )
            body = "<ul class='heads'>" + "".join(rows) + "</ul>"

        cards.append(f"<div class='feed'><h3>{escape(feed['source'])}</h3>{body}</div>")

    return "<div class='feed-grid'>" + "".join(cards) + "</div>"


def build_calendar_html(events, problems):
    """The upcoming-releases list, with any file problems shown at the top."""
    blocks = []

    if problems:
        listed = "".join(f"<li>{escape(problem)}</li>" for problem in problems)
        blocks.append(
            f"<div class='warn'><b>calendar.json needs attention</b>"
            f"<ul>{listed}</ul></div>"
        )

    today = datetime.now(TIMEZONE).date()

    for event in events:
        days_away = (event["parsed_date"] - today).days
        if days_away == 0:
            when = "today"
        elif days_away == 1:
            when = "tomorrow"
        else:
            when = f"in {days_away} days"

        # An unverified date is flagged in the open, with a link to check it.
        badge = ""
        if not event.get("verified"):
            check_at = event.get("check_at", "")
            if check_at.startswith(("http://", "https://")):
                badge = (
                    f"<a class='unconfirmed' href='{escape(check_at, quote=True)}' "
                    f"target='_blank' rel='noopener noreferrer'>date unconfirmed - verify</a>"
                )
            else:
                badge = "<span class='unconfirmed'>date unconfirmed</span>"

        blocks.append(
            "<div class='event'>"
            f"<div class='event-head'>"
            f"<span class='event-date'>{escape(event['date'])}</span>"
            f"<span class='event-when'>{when}</span>{badge}</div>"
            f"<div class='event-name'>{escape(event['name'])}</div>"
            f"<p class='event-why'>{escape(event['why'])}</p>"
            f"<p class='imp hi'><b>Higher than expected:</b> {escape(event['if_higher'])}</p>"
            f"<p class='imp lo'><b>Lower than expected:</b> {escape(event['if_lower'])}</p>"
            "</div>"
        )

    if not events and not problems:
        blocks.append("<p class='missing'>No upcoming events.</p>")

    return "".join(blocks)


def build_yield_card(yield_data):
    """The 10-year yield block. Shows a placeholder if the fetch failed."""
    if yield_data is None:
        return "<p class='missing'>10-year yield unavailable - the download returned no data.</p>"

    return (
        "<div class='hero-row'>"
        f"<span class='hero'>{yield_data['level']:.2f}%</span>"
        f"<span class='hero-side'>"
        f"1-day <b class='{change_class(yield_data['change_1d_bp'])}'>"
        f"{format_basis_points(yield_data['change_1d_bp'])}</b>"
        f" &nbsp;·&nbsp; 1-week <b class='{change_class(yield_data['change_1w_bp'])}'>"
        f"{format_basis_points(yield_data['change_1w_bp'])}</b>"
        f"</span></div>"
    )


def build_correlation_hero(correlation):
    """The current correlation reading, described in words as well as a number."""
    if correlation is None:
        return "<p class='missing'>Not enough overlapping data to compute a correlation.</p>"

    latest = correlation.iloc[-1]
    if latest <= -0.3:
        reading = "moving opposite each other - the textbook relationship is holding"
    elif latest >= 0.3:
        reading = "moving together - growth expectations, rather than rates, look like the driver"
    else:
        reading = "barely related at the moment - yields are not what is driving this fund"

    return (
        "<div class='hero-row'>"
        f"<span class='hero'>{latest:+.2f}</span>"
        f"<span class='hero-side'>{YIELD_PAIR_TICKER} returns vs daily changes in the "
        f"10-year yield, over the last {CORR_WINDOW} trading days<br>{reading}</span>"
        "</div>"
    )


def render_page(rows, risk_rows, yield_data, correlation, bar_html, trend_html,
                correlation_html, headlines_html, calendar_html, regime_html,
                curve_html, watcher_html, watcher_chart, portfolio_html,
                tv_chart, tv_calendar, as_of):
    """Assemble every piece into one HTML file and save it."""
    generated = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M")
    bar_label = BAR_LABELS[BAR_COLUMN]

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Market Brief</title>
<script src="{PLOTLY_JS_URL}"></script>
<style>{STYLES}</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>Daily Market Brief</h1>
  <p class="stamp">Market data as of the close on {as_of} &nbsp;·&nbsp;
     page generated {generated} Istanbul time</p>
</header>

<section class="card">
  <h2>Risk watcher</h2>
  {watcher_html}
  <div class="chart-scroll">{watcher_chart}</div>
  <p class="note"><b>What this number is, and is not.</b> {EXPLANATIONS['watcher']}</p>
</section>

<section class="card">
  <h2>What kind of market is this?</h2>
  {regime_html}
  <p class="note"><b>Read this panel first.</b> {EXPLANATIONS['regime']}</p>
</section>

<section class="card">
  <h2>Yield curve: 10-year minus 3-month</h2>
  <div class="chart-scroll">{curve_html}</div>
  <p class="note"><b>How to read it.</b> {EXPLANATIONS['curve']}</p>
</section>

<section class="card">
  <h2>Was today unusual?</h2>
  <div class="table-scroll">
  <table>
    <thead>
      <tr><th>Fund</th><th>1-day</th><th>Z-score</th><th></th>
          <th>Volatility ({VOL_WINDOW}d, ann.)</th><th>Vol percentile (1y)</th>
          <th>From 52w high</th></tr>
    </thead>
    <tbody>
{build_risk_table(risk_rows)}
    </tbody>
  </table>
  </div>
  <p class="note"><b>How to read it.</b> {EXPLANATIONS['risk']}</p>
</section>

<section class="card">
  <h2>Do yields still move {YIELD_PAIR_TICKER}?</h2>
  {build_correlation_hero(correlation)}
  <div class="chart-scroll">{correlation_html}</div>
  <p class="note"><b>What this is testing.</b> {EXPLANATIONS['correlation']}</p>
</section>

<section class="card">
  <h2>US 10-year Treasury yield</h2>
  {build_yield_card(yield_data)}
  <p class="note"><b>Why this matters.</b> {EXPLANATIONS['yield']}</p>
</section>

<section class="card">
  <h2>ETF closes and returns</h2>
  <div class="table-scroll">
  <table>
    <thead>
      <tr><th>Fund</th><th>Close</th><th>1-day</th><th>1-week</th><th>YTD</th></tr>
    </thead>
    <tbody>
{build_table(rows)}
    </tbody>
  </table>
  </div>
  <p class="note"><b>Price return, not total return.</b> {EXPLANATIONS['returns']}</p>
</section>

<section class="card">
  <h2>{bar_label} move by fund</h2>
  <div class="chart-scroll">{bar_html}</div>
  <p class="note"><b>How to read it.</b> {EXPLANATIONS['bars']}</p>
</section>

<section class="card">
  <h2>{" vs ".join(TREND_TICKERS)} &mdash; last {TREND_DAYS} days, rebased to 100</h2>
  <div class="chart-scroll">{trend_html}</div>
  <p class="note"><b>Why both lines start at 100.</b> {EXPLANATIONS['trend']}</p>
</section>

<section class="card">
  <h2>Your portfolio</h2>
  {portfolio_html}
  <p class="note"><b>What the risk numbers mean.</b> {EXPLANATIONS['portfolio']}</p>
</section>

<section class="card">
  <h2>Upcoming US releases</h2>
  {calendar_html}
  <p class="note"><b>Why a calendar belongs here.</b> {EXPLANATIONS['calendar']}</p>
</section>

<section class="card">
  <h2>Live charts &mdash; your holdings</h2>
  {tv_chart}
  <p class="note"><b>Why this panel is different.</b> {EXPLANATIONS['tradingview']}</p>
</section>

<section class="card">
  <h2>Economic calendar</h2>
  {tv_calendar}
</section>

<section class="card">
  <h2>Headlines</h2>
  {headlines_html}
  <p class="note"><b>Links only, on purpose.</b> {EXPLANATIONS['headlines']}</p>
</section>

<footer>
  Prices and yields from Yahoo Finance via the yfinance package. Headlines are
  links to their publishers, who own them. Calendar entries are maintained by
  hand in calendar.json; any marked unconfirmed are estimates awaiting checking.
  Every figure on
  this page was computed from data downloaded when the page was generated;
  explanatory text is fixed commentary and contains no figures. Statistics are
  descriptive summaries of past data and say nothing about what happens next.
  Not investment advice.
</footer>

</div>
</body>
</html>
"""
    # Create the docs folder if it is not there yet, then write the file.
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(page, encoding="utf-8")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def print_regime(regime, composite):
    """Print the watcher and tiles, so they can be checked against the page."""
    if composite is not None and not composite.empty:
        score = composite.iloc[-1]
        label, _ = risk_band(score)
        print(f"RISK WATCHER: {score:.0f}/100 - {label}")
        print()
    print("Market regime")
    print("-" * 79)
    if not regime:
        print("  no indicators available")
        return
    for item in regime.values():
        flag = f"  [{item['stale_days']}d old]" if item["stale_days"] > STALE_AFTER_DAYS else ""
        print(f"  {item['label']:<30} {item['value']:>8}   {item['reading']}{flag}")
    print()


def print_summary(rows, risk_rows, yield_data, correlation):
    """Print the same figures to the terminal, so the page can be checked."""
    print("ETF snapshot - source: Yahoo Finance via the yfinance package")
    print("Price changes only; dividends are not included.")
    print()
    print(f"{'Ticker':<8}{'As of':<13}{'Close':>9}{'1-day':>9}{'1-week':>9}"
          f"{'YTD':>9}{'Z':>7}{'Vol':>8}{'DD':>8}")
    print("-" * 79)

    # zip() walks two lists side by side, like mapply() in R.
    for row, risk in zip(rows, risk_rows):
        if row is None:
            continue
        z_text = format_signed(risk["z_score"], 1) if risk else "n/a"
        vol_text = format_number(risk["volatility"], 1) if risk else "n/a"
        dd_text = format_number(risk["drawdown"], 1) if risk else "n/a"
        print(
            f"{row['ticker']:<8}"
            f"{row['date'].strftime('%Y-%m-%d'):<13}"
            f"{row['close']:>9.2f}"
            f"{format_change(row['change_1d']):>9}"
            f"{format_change(row['change_1w']):>9}"
            f"{format_change(row['change_ytd']):>9}"
            f"{z_text:>7}{vol_text:>8}{dd_text:>8}"
        )

    print()
    if yield_data is None:
        print("10-year Treasury yield: no data returned")
    else:
        print(
            f"US 10-year Treasury yield: {yield_data['level']:.2f}%  "
            f"(1-day {format_basis_points(yield_data['change_1d_bp'])}, "
            f"1-week {format_basis_points(yield_data['change_1w_bp'])})"
        )

    if correlation is None:
        print(f"{YIELD_PAIR_TICKER} vs yield correlation: not enough overlapping data")
    else:
        print(f"{YIELD_PAIR_TICKER} vs 10y yield, {CORR_WINDOW}-day correlation: "
              f"{correlation.iloc[-1]:+.2f}")


def main():
    """Fetch everything, compute the statistics, print them, then write the page."""
    print("Fetching prices...")

    # A list comprehension: one fetch per ticker, results collected into a list.
    # R's closest relative is lapply(TICKERS, fetch_ticker).
    rows = [fetch_ticker(ticker) for ticker in TICKERS]

    failed = [t for t, r in zip(TICKERS, rows) if r is None]
    if failed:
        print(f"WARNING: no data returned for {', '.join(failed)}")

    # If EVERY download failed, stop rather than publish a page of "n/a". When
    # this script runs unattended at six in the morning, a loud failure that
    # leaves yesterday's good page in place beats a silent one that replaces it
    # with an empty one. sys.exit(1) is how a program reports failure: zero
    # means success, anything else means trouble, and GitHub watches for it.
    if all(row is None for row in rows):
        print("ERROR: no price data at all - refusing to publish an empty page.")
        sys.exit(1)

    yield_data = fetch_yield()

    # The reference date comes from the ETF data, not the clock, so staleness is
    # judged against what the market actually did rather than what day it is.
    dated_rows = [row for row in rows if row is not None]
    reference_date = (dated_rows[0]["date"].date() if dated_rows
                      else datetime.now(TIMEZONE).date())

    print("Fetching risk regime indicators...")
    regime, curve_series, scores, composite = fetch_risk_regime(reference_date)
    for key, item in regime.items():
        if item["stale_days"] > STALE_AFTER_DAYS:
            print(f"WARNING: {key} data is {item['stale_days']} days old - flagged on the page")

    print("Computing statistics...")
    risk_rows = [compute_risk(row) if row is not None else None for row in rows]

    print("Fetching headlines...")
    feeds = fetch_headlines()
    for feed in feeds:
        if feed["error"]:
            print(f"WARNING: {feed['source']} feed failed - {feed['error']}")
        elif not feed["items"]:
            print(f"WARNING: {feed['source']} returned no recent headlines")

    print("Reading positions.json...")
    settings, holdings, position_problems = load_positions(POSITIONS_PATH)
    if POSITIONS_CSV_URL:
        # The sheet is the live source when one is configured; positions.json is
        # then only used for the reporting-currency setting.
        holdings, position_problems = load_positions_from_sheet(POSITIONS_CSV_URL)
        print(f"  holdings source: published sheet ({len(holdings)} rows read)")
        for holding in holdings:
            print(f"    {holding['symbol']:10} qty={holding['quantity']:<12g} "
                  f"paid={holding['buy_price']:<12g} on {holding['buy_date']}")
    for problem in position_problems:
        print(f"POSITIONS: {problem}")
    portfolio_rows, portfolio_totals, pricing_problems = build_portfolio(settings, holdings)
    for problem in pricing_problems:
        print(f"POSITIONS: {problem}")

    print("Reading calendar.json...")
    calendar_events, calendar_problems = load_calendar(CALENDAR_PATH)
    for problem in calendar_problems:
        print(f"CALENDAR: {problem}")

    correlation = None
    pair = next((r for r in rows if r is not None and r["ticker"] == YIELD_PAIR_TICKER), None)
    if pair is not None and yield_data is not None:
        correlation = rolling_yield_correlation(
            pair["history"], yield_data["history"], CORR_WINDOW
        )

    print()
    print_regime(regime, composite)
    print_summary(rows, risk_rows, yield_data, correlation)

    # The "as of" date shown on the page comes from the data itself, never from
    # today's clock - those differ at weekends and on market holidays.
    dated = [row for row in rows if row is not None]
    as_of = dated[0]["date"].strftime("%Y-%m-%d") if dated else "unknown"

    render_page(
        rows=rows,
        risk_rows=risk_rows,
        yield_data=yield_data,
        correlation=correlation,
        bar_html=build_bar_chart(dated),
        trend_html=build_trend_chart(rebased_trend(rows, TREND_TICKERS, TREND_DAYS)),
        correlation_html=build_correlation_chart(correlation),
        headlines_html=build_headlines_html(feeds),
        calendar_html=build_calendar_html(calendar_events, calendar_problems),
        regime_html=build_regime_html(regime),
        curve_html=build_curve_chart(curve_series),
        watcher_html=build_watcher_html(composite, scores),
        watcher_chart=build_watcher_chart(composite),
        tv_chart=build_tradingview_chart(portfolio_rows),
        tv_calendar=build_tradingview_calendar(),
        portfolio_html=build_portfolio_html(settings, portfolio_rows,
                                            portfolio_totals,
                                            position_problems + pricing_problems),
        as_of=as_of,
    )

    print()
    print(f"Page written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
