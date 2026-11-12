from twtm import db

KEY = ("trade_date", "product", "investor")


def row(source, long_oi):
    return {"trade_date": "2023-01-03", "product": "TX", "investor": "foreign",
            "long_oi": long_oi, "short_oi": 1, "source": source, "fetched_at": "t"}


def get(conn):
    return conn.execute("SELECT source, long_oi FROM fut_institutional").fetchone()


def test_official_overwrites_third_party(conn):
    db.upsert(conn, "fut_institutional", [row("finmind", 10)], KEY)
    db.upsert(conn, "fut_institutional", [row("taifex", 20)], KEY)
    assert tuple(get(conn)) == ("taifex", 20)


def test_third_party_does_not_overwrite_official(conn):
    db.upsert(conn, "fut_institutional", [row("taifex", 20)], KEY)
    written = db.upsert(conn, "fut_institutional", [row("finmind", 10)], KEY)
    assert written == 0
    assert tuple(get(conn)) == ("taifex", 20)


def test_same_source_updates(conn):
    db.upsert(conn, "fut_institutional", [row("taifex", 20)], KEY)
    db.upsert(conn, "fut_institutional", [row("taifex", 21)], KEY)
    assert tuple(get(conn)) == ("taifex", 21)
