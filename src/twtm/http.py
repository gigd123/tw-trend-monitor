"""HTTP 抓取：每個來源各自的 headers、限速與重試。

實測（docs/data-feasibility.md）：
- FRED 只接受 requests 預設 UA；Yahoo 會擋 requests 預設 UA。
- 證交所 WAF：連續請求回 307 導向安全頁。
- 期交所：查詢超出 3 年視窗時回 HTML（JS alert），不是空 CSV。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """重試後仍失敗。"""


class RateLimited(FetchError):
    """來源回報額度用盡（例如 FinMind 402）。不應重試，應中止並稍後續跑。"""


@dataclass
class SourcePolicy:
    headers: dict = field(default_factory=dict)
    min_interval: float = 3.0   # 同一來源兩次請求的最短間隔（秒）
    retries: int = 3
    backoff: float = 10.0       # 第 n 次重試前等待 backoff * n 秒
    timeout: float = 90.0


POLICIES: dict[str, SourcePolicy] = {
    "taifex": SourcePolicy(headers={"User-Agent": "Mozilla/5.0"}, min_interval=3.0),
    "finmind": SourcePolicy(headers={}, min_interval=2.0),
    # 證交所 WAF 擋下後要等一陣子才會放行，重試次數與退避都拉長
    "twse": SourcePolicy(headers={"User-Agent": "Mozilla/5.0"}, min_interval=5.0,
                         retries=6, backoff=20.0),
    "tpex": SourcePolicy(headers={"User-Agent": "Mozilla/5.0"}, min_interval=2.0),
    "fred": SourcePolicy(headers={}, min_interval=1.0),
    "yahoo": SourcePolicy(headers={"User-Agent": "Mozilla/5.0"}, min_interval=1.0),
}


class Fetcher:
    def __init__(self, sleep=time.sleep, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._last: dict[str, float] = {}
        self._sleep = sleep

    def _throttle(self, source: str, policy: SourcePolicy) -> None:
        last = self._last.get(source)
        if last is not None:
            wait = policy.min_interval - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[source] = time.monotonic()

    def request(self, source: str, method: str, url: str, *,
                params: dict | None = None, data: dict | None = None) -> requests.Response:
        policy = POLICIES[source]
        last_err: Exception | None = None
        for attempt in range(policy.retries):
            self._throttle(source, policy)
            try:
                r = self._session.request(
                    method, url, params=params, data=data, headers=policy.headers,
                    timeout=policy.timeout, allow_redirects=False,
                )
                if r.status_code == 402:
                    raise RateLimited(f"{source}: HTTP 402 額度用盡")
                if r.status_code == 200:
                    return r
                last_err = FetchError(f"{source}: HTTP {r.status_code} {url}")
            except RateLimited:
                raise
            except requests.RequestException as e:
                last_err = e
            if attempt < policy.retries - 1:
                log.warning("%s 第 %d 次失敗：%s，重試中", source, attempt + 1, last_err)
                self._sleep(policy.backoff * (attempt + 1))
        raise FetchError(f"{source}: 重試 {policy.retries} 次仍失敗：{last_err}")
