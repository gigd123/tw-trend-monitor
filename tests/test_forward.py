import json
from datetime import date, datetime

import pandas as pd

from twtm import TAIPEI, db
from twtm.risk.forward import _late, record

T = "2026-11-12T00:00:00+00:00"


def _seed(conn, n=90, start="2026-07-01"):
    rows = []
    for i, d in enumerate(pd.bdate_range(start, periods=n)):
        c = 100 + i if i < 70 else 170 - 3 * (i - 70)
        rows.append({"symbol": "TAIEX", "trade_date": d.date().isoformat(), "open": c,
                     "high": c + 1, "low": c - 1, "close": c, "volume": 1, "turnover": 1.0,
                     "source": "twse", "fetched_at": T})
    db.upsert(conn, "price_daily", rows, ("symbol", "trade_date"))
    return pd.bdate_range(start, periods=n)


def test_record_is_insert_only_and_tracks_all_combos(conn):
    dates = _seed(conn)
    now = datetime(2026, 11, 12, 15, 40, tzinfo=TAIPEI)
    added = record(conn, symbols=("TAIEX",), start=dates[80].date(), now=now)
    assert len(added) == 10
    row = conn.execute("SELECT * FROM forward_log ORDER BY trade_date DESC").fetchone()
    tiers = json.loads(row["tiers"])
    assert sum(k.startswith("bt-v1|") for k in tiers) == 27
    assert sum(k.startswith("bt-v2|") for k in tiers) == 9
    assert row["level"] == 3                      # 最後 20 天持續下跌

    # 第二次執行：不新增、不覆寫
    conn.execute("UPDATE forward_log SET label='原始' WHERE trade_date=?", (row["trade_date"],))
    assert record(conn, symbols=("TAIEX",), start=dates[80].date(), now=now) == []
    assert conn.execute("SELECT label FROM forward_log WHERE trade_date=?",
                        (row["trade_date"],)).fetchone()[0] == "原始"


def test_late_flag():
    d = date(2026, 11, 13)                         # 週五
    assert not _late(d, datetime(2026, 11, 13, 21, 30, tzinfo=TAIPEI))
    assert not _late(d, datetime(2026, 11, 16, 8, 59, tzinfo=TAIPEI))   # 下週一開盤前
    assert _late(d, datetime(2026, 11, 16, 9, 0, tzinfo=TAIPEI))
