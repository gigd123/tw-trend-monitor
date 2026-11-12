"""訊號計算。

原則：
- 訊號函式只吃一條 OHLCV 序列（pandas DataFrame），不寫死標的，個股可以直接套用。
- 只用訊號日收盤以前的資料；需要未來幾天才能確認的訊號（例如缺口未回補），
  訊號日就是確認那天，不回填。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).with_name("default.toml")


def load_config(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, "rb") as f:
        return tomllib.load(f)
