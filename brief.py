"""Daily market brief.

Stage 3: an analysis tool, not a data display.

WHY THIS EXISTS AT ALL
  Closing prices are one click away on any finance site. What is NOT one click
  away is context: whether today's move was statistically unusual, whether the
  textbook relationship between bond yields and technology stocks is actually
  holding right now, and where each fund sits in its own volatility and
  drawdown history. All of that needs a year of history and some arithmetic on
  top of it, which is what this script does.

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

import json                       # reads calendar.json
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
                correlation_html, headlines_html, calendar_html, as_of):
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
  <h2>Upcoming US releases</h2>
  {calendar_html}
  <p class="note"><b>Why a calendar belongs here.</b> {EXPLANATIONS['calendar']}</p>
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

    yield_data = fetch_yield()

    print("Computing statistics...")
    risk_rows = [compute_risk(row) if row is not None else None for row in rows]

    print("Fetching headlines...")
    feeds = fetch_headlines()
    for feed in feeds:
        if feed["error"]:
            print(f"WARNING: {feed['source']} feed failed - {feed['error']}")
        elif not feed["items"]:
            print(f"WARNING: {feed['source']} returned no recent headlines")

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
        as_of=as_of,
    )

    print()
    print(f"Page written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
