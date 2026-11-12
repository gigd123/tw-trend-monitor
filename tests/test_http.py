import pytest
import requests

from twtm.http import FetchError, Fetcher, RateLimited


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, *a, **kw):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        r = requests.Response()
        r.status_code = o
        r._content = b"ok"
        return r


def make(outcomes):
    s = FakeSession(outcomes)
    return Fetcher(sleep=lambda _: None, session=s), s


def test_retries_waf_redirect_then_succeeds():
    f, s = make([307, requests.ConnectionError("reset"), 200])
    assert f.request("twse", "GET", "u").content == b"ok"
    assert s.calls == 3


def test_gives_up_after_retries():
    f, s = make([307, 307, 307])
    with pytest.raises(FetchError):
        f.request("taifex", "GET", "u")
    assert s.calls == 3


def test_rate_limit_is_not_retried():
    f, s = make([402, 200])
    with pytest.raises(RateLimited):
        f.request("finmind", "GET", "u")
    assert s.calls == 1
