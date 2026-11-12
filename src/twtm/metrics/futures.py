"""期貨籌碼指標。

非三大法人多空比（估算；口徑見 docs/data-feasibility.md §4）：
    全市場未平倉 OI = 該商品一般時段所有到期月份（含週契約）未沖銷契約數加總
    非法人多空比    = (法人空單 − 法人多單) / OI
正值＝非法人偏多。這是估算值，不等於散戶帳戶的實際多空比。
程式中的 retail_* 名稱沿用早期命名，意義即「非三大法人」。

資料不完整（缺任一身分別或缺 OI）時回傳 None，不以 0 或推估值代替。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

INVESTOR_SET = {"dealer", "trust", "foreign"}


@dataclass(frozen=True)
class FuturesPosition:
    trade_date: str
    product: str
    oi_total: int | None
    foreign_long: int | None
    foreign_short: int | None
    inst_long: int | None
    inst_short: int | None
    complete: bool          # 三種身分別與 OI 都有資料
    sources: str            # 使用的來源，例如 "taifex" 或 "finmind,taifex"

    @property
    def foreign_net(self) -> int | None:
        if self.foreign_long is None or self.foreign_short is None:
            return None
        return self.foreign_long - self.foreign_short

    @property
    def retail_net(self) -> int | None:
        """非法人淨多單口數 = 法人淨空單。"""
        if not self.complete:
            return None
        return self.inst_short - self.inst_long

    @property
    def retail_ratio(self) -> float | None:
        if not self.complete or not self.oi_total:
            return None
        return self.retail_net / self.oi_total


_SQL = """
WITH inst AS (
    SELECT trade_date, product,
           COUNT(DISTINCT investor)                                   AS n_inv,
           SUM(long_oi)                                               AS inst_long,
           SUM(short_oi)                                              AS inst_short,
           SUM(CASE WHEN investor='foreign' THEN long_oi END)         AS foreign_long,
           SUM(CASE WHEN investor='foreign' THEN short_oi END)        AS foreign_short,
           GROUP_CONCAT(DISTINCT source)                              AS src
    FROM fut_institutional
    WHERE product = :product AND trade_date BETWEEN :start AND :end
    GROUP BY trade_date, product
),
oi AS (
    SELECT trade_date, product, SUM(oi) AS oi_total, COUNT(oi) AS n_oi,
           GROUP_CONCAT(DISTINCT source) AS src
    FROM fut_daily f
    WHERE product = :product AND session = 'regular'
      AND trade_date BETWEEN :start AND :end
      -- 同一天只用單一來源加總，避免兩來源合約清單不同造成重複計算
      AND (source = 'taifex' OR NOT EXISTS (
            SELECT 1 FROM fut_daily t
            WHERE t.trade_date = f.trade_date AND t.product = f.product
              AND t.source = 'taifex'))
    GROUP BY trade_date, product
),
dates AS (
    SELECT trade_date FROM inst UNION SELECT trade_date FROM oi
)
SELECT d.trade_date, :product AS product,
       oi.oi_total, oi.n_oi, inst.n_inv, inst.inst_long, inst.inst_short,
       inst.foreign_long, inst.foreign_short,
       COALESCE(inst.src, '') || ',' || COALESCE(oi.src, '') AS src
FROM dates d
LEFT JOIN inst ON inst.trade_date = d.trade_date
LEFT JOIN oi   ON oi.trade_date   = d.trade_date
ORDER BY d.trade_date
"""


def positions(conn: sqlite3.Connection, product: str, start: str = "0000-00-00",
              end: str = "9999-12-31") -> list[FuturesPosition]:
    out = []
    for r in conn.execute(_SQL, {"product": product, "start": start, "end": end}):
        complete = (r["n_inv"] == len(INVESTOR_SET) and bool(r["n_oi"])
                    and r["oi_total"] is not None)
        sources = ",".join(sorted({s for s in r["src"].split(",") if s}))
        out.append(FuturesPosition(
            trade_date=r["trade_date"], product=product,
            oi_total=r["oi_total"] if r["n_oi"] else None,
            foreign_long=r["foreign_long"], foreign_short=r["foreign_short"],
            inst_long=r["inst_long"], inst_short=r["inst_short"],
            complete=complete, sources=sources,
        ))
    return out
