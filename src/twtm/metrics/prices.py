"""價格序列讀取、除權息還原、資料品質檢查。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bar:
    trade_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    turnover: float | None
    ex_date: bool = False   # 當日為除權息日（跳空可能是除權息造成，非市場訊號）
    factor: float = 1.0     # 還原因子（未調整價格 × factor = 還原價格）


def load_bars(conn: sqlite3.Connection, symbol: str, *, adjust: bool = True,
              start: str = "0000-00-00", end: str = "9999-12-31") -> list[Bar]:
    """讀取日 K。adjust=True 時以除權息資料向前還原價格（量不調整）。

    還原因子 = Π（之後每個除權息日的 參考價 / 除權息前收盤價）。
    """
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume, turnover FROM price_daily "
        "WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (symbol, start, end)).fetchall()
    actions = conn.execute(
        "SELECT ex_date, ref_price / prev_close AS f FROM corporate_action "
        "WHERE symbol=? AND prev_close > 0 AND ref_price > 0 ORDER BY ex_date DESC",
        (symbol,)).fetchall()
    ex_dates = {a["ex_date"] for a in actions}

    factors: dict[str, float] = {}
    if adjust and actions:
        f, i = 1.0, 0
        for r in reversed(rows):           # 由新到舊
            while i < len(actions) and actions[i]["ex_date"] > r["trade_date"]:
                f *= actions[i]["f"]
                i += 1
            factors[r["trade_date"]] = f

    out = []
    for r in rows:
        f = factors.get(r["trade_date"], 1.0)
        adj = (lambda v: None if v is None else v * f)  # noqa: E731
        out.append(Bar(r["trade_date"], adj(r["open"]), adj(r["high"]), adj(r["low"]),
                       adj(r["close"]), r["volume"], r["turnover"],
                       r["trade_date"] in ex_dates, f))
    return out


@dataclass
class QualityReport:
    symbol: str
    first: str | None = None
    last: str | None = None
    days: int = 0
    missing_dates: list[str] = field(default_factory=list)   # 台股交易日曆中缺的日期
    missing_ohlc: list[str] = field(default_factory=list)
    missing_turnover: list[str] = field(default_factory=list)
    ohlc_violations: list[str] = field(default_factory=list)  # 高低價與開收盤矛盾
    big_moves: list[str] = field(default_factory=list)        # 非除權息日單日變動超過門檻

    @property
    def ok(self) -> bool:
        return not (self.missing_dates or self.missing_ohlc or self.ohlc_violations)


TW_SYMBOLS_CALENDAR = ("TAIEX", "TPEX")
BIG_MOVE = 0.105   # 台股漲跌幅限制 10%（2015-06 以前 7%），超過即疑似資料錯誤


def check(conn: sqlite3.Connection, symbol: str, market: str, needs_turnover: bool,
          close_only: bool = False) -> QualityReport:
    """close_only：只有收盤價的序列（例如報酬指數），不檢查 OHLC 與成交值。"""
    rep = QualityReport(symbol)
    bars = load_bars(conn, symbol, adjust=True)
    if not bars:
        return rep
    rep.first, rep.last, rep.days = bars[0].trade_date, bars[-1].trade_date, len(bars)

    if market in ("TWSE", "TPEx"):
        cal = {r[0] for r in conn.execute(
            f"SELECT DISTINCT trade_date FROM price_daily WHERE symbol IN "
            f"({','.join('?' * len(TW_SYMBOLS_CALENDAR))}) AND trade_date BETWEEN ? AND ?",
            (*TW_SYMBOLS_CALENDAR, rep.first, rep.last))}
        have = {b.trade_date for b in bars}
        rep.missing_dates = sorted(cal - have)

    prev = None
    if close_only:
        rep.missing_ohlc = [b.trade_date for b in bars if b.close is None]
        return rep
    for b in bars:
        if None in (b.open, b.high, b.low, b.close):
            rep.missing_ohlc.append(b.trade_date)
        else:
            if b.low > min(b.open, b.close) + 1e-9 or b.high < max(b.open, b.close) - 1e-9:
                rep.ohlc_violations.append(b.trade_date)
            if (prev is not None and prev.close and not b.ex_date and market != "US"
                    and abs(b.close / prev.close - 1) > BIG_MOVE):
                rep.big_moves.append(b.trade_date)
        if needs_turnover and b.turnover is None:
            rep.missing_turnover.append(b.trade_date)
        prev = b
    return rep
