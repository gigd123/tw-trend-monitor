"""外部資料來源。每個模組分成純解析函式（可離線測試）與抓取函式。"""

from dataclasses import dataclass


class ParseError(ValueError):
    """回應格式不符預期（端點改版、欄位變動）。"""


@dataclass(frozen=True)
class Product:
    code: str          # 系統內部代碼
    name: str          # 期交所中文名稱（法人 CSV 的「商品名稱」）
    inst_id: str       # 期交所法人下載的 commodityId
    daily_id: str      # 期交所行情下載的 commodity_id
    finmind_id: str
    listed: str        # 上市日


PRODUCTS: dict[str, Product] = {
    "TX": Product("TX", "臺股期貨", "TXF", "TX", "TX", "1998-07-21"),
    "MTX": Product("MTX", "小型臺指期貨", "MXF", "MTX", "MTX", "2001-04-09"),
    "TMF": Product("TMF", "微型臺指期貨", "TMF", "TMF", "TMF", "2024-07-29"),
}

PRODUCT_BY_NAME = {p.name: p.code for p in PRODUCTS.values()}

# 身分別標籤：期交所用「外資及陸資」，FinMind 用「外資」
INVESTORS = {
    "自營商": "dealer",
    "投信": "trust",
    "外資及陸資": "foreign",
    "外資": "foreign",
}
