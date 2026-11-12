from pathlib import Path

import pytest

from twtm import db

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200


class FakeFetcher:
    """依請求內容回傳預先準備的回應，並記錄所有請求。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def request(self, source, method, url, *, params=None, data=None):
        call = {"source": source, "url": url, **(params or {}), **(data or {})}
        self.calls.append(call)
        return FakeResponse(self.responder(call))
