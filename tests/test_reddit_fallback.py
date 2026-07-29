"""Tests for the RSS-first Reddit fetcher, its 429 backoff, the opt-in JSON
path's degradation (#862), and chunked-transfer error handling (#1024)."""

from __future__ import annotations

import http.client
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from tradingagents.dataflows import reddit

_SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>NVDA earnings beat, stock pops</title>
    <published>2026-05-20T14:30:00+00:00</published>
    <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Great &lt;b&gt;quarter&lt;/b&gt; for NVDA&amp;#39;s datacenter unit.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;</content>
  </entry>
  <entry>
    <title>Is NVDA overvalued?</title>
    <published>2026-05-19T09:00:00Z</published>
    <content type="html">&lt;p&gt;Forward P/E discussion&lt;/p&gt;</content>
  </entry>
</feed>
"""


@pytest.fixture(autouse=True)
def _reset_reddit_state():
    """Throttle clock and feed cache are module-global; keep tests order-independent."""
    reddit._last_request_at = 0.0
    reddit._feed_cache.clear()
    yield
    reddit._last_request_at = 0.0
    reddit._feed_cache.clear()


def _resp(read_fn):
    """A minimal context-manager response whose read() runs ``read_fn``."""
    class _Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

        def read(self_inner):
            return read_fn()
    return _Resp()


def _atom_resp():
    return _resp(lambda: _SAMPLE_ATOM.encode("utf-8"))


def _raise(exc):
    def _r():
        raise exc
    return _resp(_r)


@pytest.mark.unit
class TestIsoToTimestamp:
    def test_parses_offset_and_z(self):
        assert reddit._iso_to_timestamp("2026-05-20T14:30:00+00:00") > 0
        assert reddit._iso_to_timestamp("2026-05-19T09:00:00Z") > 0

    def test_none_and_garbage_return_none(self):
        assert reddit._iso_to_timestamp(None) is None
        assert reddit._iso_to_timestamp("not-a-date") is None


@pytest.mark.unit
class TestStripHtml:
    def test_extracts_between_sc_markers_and_unescapes(self):
        raw = "<!-- SC_OFF --><div class=\"md\"><p>Great <b>quarter</b> &amp; more</p></div><!-- SC_ON -->"
        assert reddit._strip_html(raw) == "Great quarter & more"

    def test_empty(self):
        assert reddit._strip_html("") == ""


@pytest.mark.unit
class TestRssParsing:
    def test_parses_atom_entries(self):
        with patch.object(reddit, "urlopen", return_value=_atom_resp()):
            posts = reddit._fetch_subreddit_rss("NVDA", "stocks", limit=5, timeout=5.0)
        assert len(posts) == 2
        assert posts[0]["title"] == "NVDA earnings beat, stock pops"
        assert posts[0]["source"] == "rss"
        assert posts[0]["score"] is None
        assert posts[0]["num_comments"] is None
        assert posts[0]["created_utc"] > 0
        assert "datacenter unit" in posts[0]["selftext"]

    def test_malformed_xml_fails_open_as_unavailable(self):
        # A parse failure means the feed's content is unknown — that's a fetch
        # failure (None), not a genuine "no matching posts" ([]).
        with patch.object(reddit, "urlopen", return_value=_resp(lambda: b"<<not xml>>")):
            assert reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0) is None


@pytest.mark.unit
class TestFetchSubredditIsRssFirst:
    """The default per-subreddit fetch goes straight to RSS — it must not hit
    the WAF-blocked JSON endpoint, which only burned rate-limit budget."""

    def test_delegates_to_rss_without_touching_json(self):
        sentinel = [{"title": "x", "source": "rss", "score": None,
                     "num_comments": None, "created_utc": None, "selftext": ""}]
        with patch.object(reddit, "_fetch_subreddit_rss", return_value=sentinel) as rss, \
             patch.object(reddit, "urlopen",
                          side_effect=AssertionError("JSON endpoint must not be called")):
            out = reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0)
        rss.assert_called_once()
        assert out is sentinel


@pytest.mark.unit
class TestJsonPathFallsBackToRss:
    """The opt-in JSON path still degrades to RSS on a 403 (kept for #862)."""

    def test_403_triggers_rss(self):
        err = HTTPError("url", 403, "Blocked", {}, None)
        rss_posts = [{"title": "x", "source": "rss", "score": None,
                      "num_comments": None, "created_utc": None, "selftext": ""}]
        with patch.object(reddit, "urlopen", side_effect=err), \
             patch.object(reddit, "_fetch_subreddit_rss", return_value=rss_posts) as rss:
            out = reddit._fetch_subreddit_json("NVDA", "stocks", 5, 5.0)
        rss.assert_called_once()
        assert out and out[0]["source"] == "rss"


@pytest.mark.unit
class TestRss429Backoff:
    def _backoff_sleeps(self, slept):
        # The throttle also sleeps (small waits); isolate the 429 backoffs.
        return [c.args[0] for c in slept.call_args_list
                if c.args[0] >= min(reddit._RSS_429_BACKOFFS)]

    def test_429_then_success_retries(self):
        err = HTTPError("url", 429, "Too Many Requests", {}, None)
        with patch.object(reddit, "urlopen", side_effect=[err, _atom_resp()]) as op, \
             patch.object(reddit.time, "sleep") as slept:
            posts = reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0)
        assert op.call_count == 2          # original + one retry sufficed
        assert self._backoff_sleeps(slept) == [reddit._RSS_429_BACKOFFS[0]]
        assert len(posts) == 2

    def test_429_exhaustion_returns_none_with_escalating_backoffs(self):
        err = HTTPError("url", 429, "Too Many Requests", {}, None)
        attempts = len(reddit._RSS_429_BACKOFFS) + 1
        with patch.object(reddit, "urlopen", side_effect=[err] * attempts) as op, \
             patch.object(reddit.time, "sleep") as slept:
            posts = reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0)
        assert op.call_count == attempts
        assert self._backoff_sleeps(slept) == list(reddit._RSS_429_BACKOFFS)
        assert posts is None               # fetch failure, NOT "no posts"

    def test_retry_after_header_wins_when_longer(self):
        err = HTTPError("url", 429, "Too Many Requests", {"Retry-After": "12"}, None)
        with patch.object(reddit, "urlopen", side_effect=[err, _atom_resp()]), \
             patch.object(reddit.time, "sleep") as slept:
            reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0)
        assert 12.0 in self._backoff_sleeps(slept)

    def test_backoff_wins_when_retry_after_is_shorter(self):
        err = HTTPError("url", 429, "Too Many Requests", {"Retry-After": "1"}, None)
        with patch.object(reddit, "urlopen", side_effect=[err, _atom_resp()]), \
             patch.object(reddit.time, "sleep") as slept:
            reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0)
        assert reddit._RSS_429_BACKOFFS[0] in self._backoff_sleeps(slept)


@pytest.mark.unit
class TestChunkedTransferErrorsHandled:
    """IncompleteRead/RemoteDisconnected come from http.client and are NOT
    OSErrors, so they were previously uncaught and crashed the pipeline (#1024)."""

    def test_rss_incomplete_read_degrades_to_unavailable(self):
        with patch.object(reddit, "urlopen", return_value=_raise(http.client.IncompleteRead(b""))):
            assert reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0) is None

    def test_json_incomplete_read_falls_back_to_rss(self):
        with patch.object(reddit, "urlopen", return_value=_raise(http.client.IncompleteRead(b""))), \
             patch.object(reddit, "_fetch_subreddit_rss", return_value=[]) as rss:
            reddit._fetch_subreddit_json("NVDA", "stocks", 5, 5.0)
        rss.assert_called_once()


@pytest.mark.unit
class TestFormatterHandlesRssPosts:
    def test_rss_posts_omit_fake_counts_and_note_source(self):
        rss_posts = [{
            "title": "NVDA pops", "score": None, "num_comments": None,
            "created_utc": reddit._iso_to_timestamp("2026-05-20T14:30:00Z"),
            "selftext": "great quarter", "source": "rss",
        }]
        with patch.object(reddit, "_fetch_subreddit", return_value=rss_posts):
            out = reddit.fetch_reddit_posts("NVDA", subreddits=("stocks",), inter_request_delay=0)
        assert "via RSS feed" in out
        assert "↑" not in out  # no fake score arrow
        assert "NVDA pops" in out
        assert "great quarter" in out

    def test_json_posts_still_show_counts(self):
        json_posts = [{
            "title": "NVDA pops", "score": 1234, "num_comments": 56,
            "created_utc": reddit._iso_to_timestamp("2026-05-20T14:30:00Z"),
            "selftext": "",
        }]
        with patch.object(reddit, "_fetch_subreddit", return_value=json_posts):
            out = reddit.fetch_reddit_posts("NVDA", subreddits=("stocks",), inter_request_delay=0)
        assert "1234↑" in out
        assert "56c" in out
        assert "via RSS" not in out


@pytest.mark.unit
class TestPlaceholdersDistinguishFailureFromEmpty:
    """A failed fetch must never read as "no posts found" — the LLM would
    report absent discussion as fact instead of lowering its confidence."""

    def test_fetch_failure_renders_unavailable(self):
        with patch.object(reddit, "_fetch_subreddit", return_value=None):
            out = reddit.fetch_reddit_posts("NVDA", subreddits=("stocks",), inter_request_delay=0)
        assert "temporarily unavailable" in out
        assert "no posts found" not in out

    def test_genuine_empty_renders_no_posts(self):
        with patch.object(reddit, "_fetch_subreddit", return_value=[]):
            out = reddit.fetch_reddit_posts("NVDA", subreddits=("stocks",), inter_request_delay=0)
        assert "no Reddit posts found" in out
        assert "temporarily unavailable" not in out

    def test_mixed_failure_and_posts_keeps_both_blocks(self):
        posts = [{"title": "NVDA pops", "score": None, "num_comments": None,
                  "created_utc": None, "selftext": "", "source": "rss"}]
        results = {"stocks": None, "investing": posts}

        def fake_fetch(t, sub, limit, timeout):
            return results[sub]

        with patch.object(reddit, "_fetch_subreddit", side_effect=fake_fetch):
            out = reddit.fetch_reddit_posts(
                "NVDA", subreddits=("stocks", "investing"), inter_request_delay=0
            )
        assert "r/stocks: <temporarily unavailable" in out
        assert "NVDA pops" in out


@pytest.mark.unit
class TestThrottle:
    def test_requests_are_paced_process_wide(self):
        # Two back-to-back fetches must be spaced _MIN_REQUEST_INTERVAL apart
        # so concurrent webapp runs can't hammer Reddit from one IP.
        sleeps = []
        with patch.object(reddit, "urlopen", side_effect=[_atom_resp(), _atom_resp()]), \
             patch.object(reddit.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            reddit._fetch_subreddit_rss("NVDA", "stocks", 5, 5.0)
            reddit._fetch_subreddit_rss("NVDA", "investing", 5, 5.0)
        assert any(0 < s <= reddit._MIN_REQUEST_INTERVAL for s in sleeps)


@pytest.mark.unit
class TestFeedCache:
    _POSTS = [{"title": "x", "source": "rss", "score": None,
               "num_comments": None, "created_utc": None, "selftext": ""}]

    def test_second_fetch_is_served_from_cache(self):
        with patch.object(reddit, "_fetch_subreddit_rss", return_value=self._POSTS) as rss:
            first = reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0)
            second = reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0)
        rss.assert_called_once()
        assert first == second == self._POSTS

    def test_empty_result_is_cached_failure_is_not(self):
        # [] is a real answer (feed fetched, no matches) worth caching;
        # None is a failure and must be retried on the next call.
        with patch.object(reddit, "_fetch_subreddit_rss", side_effect=[[], []]) as rss:
            assert reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0) == []
            assert reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0) == []
        rss.assert_called_once()

        reddit._feed_cache.clear()
        with patch.object(reddit, "_fetch_subreddit_rss", side_effect=[None, self._POSTS]) as rss:
            assert reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0) is None
            assert reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0) == self._POSTS
        assert rss.call_count == 2

    def test_cache_expires_after_ttl(self):
        with patch.object(reddit, "_fetch_subreddit_rss", return_value=self._POSTS) as rss:
            reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0)
            key = ("NVDA", "stocks", 5)
            stored_at, posts = reddit._feed_cache[key]
            reddit._feed_cache[key] = (stored_at - reddit._FEED_CACHE_TTL - 1.0, posts)
            reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0)
        assert rss.call_count == 2

    def test_different_tickers_do_not_share_entries(self):
        with patch.object(reddit, "_fetch_subreddit_rss", return_value=self._POSTS) as rss:
            reddit._fetch_subreddit("NVDA", "stocks", 5, 5.0)
            reddit._fetch_subreddit("AMD", "stocks", 5, 5.0)
        assert rss.call_count == 2


@pytest.mark.unit
class TestCryptoSearchTerm:
    """A crypto pair (BTC-USD) barely matches Reddit text; search the base (#1113)."""

    def _captured_ticker(self, ticker):
        seen = {}

        def fake_fetch(t, sub, limit, timeout):
            seen["ticker"] = t
            return []

        with patch.object(reddit, "_fetch_subreddit", side_effect=fake_fetch):
            reddit.fetch_reddit_posts(ticker, subreddits=("stocks",), inter_request_delay=0)
        return seen["ticker"]

    def test_crypto_pair_searches_base(self):
        assert self._captured_ticker("BTC-USD") == "BTC"

    def test_equity_passes_through(self):
        assert self._captured_ticker("NVDA") == "NVDA"
