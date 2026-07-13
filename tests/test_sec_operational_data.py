import json
from unittest.mock import Mock

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.sec_filings import get_sec_operational_evidence


def _response(*, payload=None, text="", status=200):
    response = Mock()
    response.status_code = status
    response.text = text
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_sec_provider_filters_future_filings_and_preserves_source_url(monkeypatch):
    set_config({"sec_user_agent": "Test test@example.com", "operational_max_filings": 4})
    tickers = {"0": {"ticker": "TEST", "cik_str": 1234, "title": "Test Company"}}
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000001234-24-000001", "0000001234-24-000002"],
                "filingDate": ["2024-03-01", "2024-04-15"],
                "reportDate": ["2023-12-31", "2024-03-31"],
                "form": ["10-K", "10-Q"],
                "primaryDocument": ["test-10k.htm", "future-10q.htm"],
            }
        }
    }
    filing = "<html><body>The company reported backlog and supply chain bottlenecks.</body></html>"

    def fake_get(url, **kwargs):
        if url.endswith("company_tickers.json"):
            return _response(payload=tickers)
        if "submissions" in url:
            return _response(payload=submissions)
        assert "future-10q" not in url
        return _response(text=filing)

    monkeypatch.setattr("requests.get", fake_get)
    payload = json.loads(get_sec_operational_evidence("TEST", "2024-03-31"))
    assert payload["status"] == "ok"
    assert payload["evidence_records"]
    assert all(record["filing_date"] <= "2024-03-31" for record in payload["evidence_records"])
    assert all(record["source_url"].startswith("https://www.sec.gov/Archives/") for record in payload["evidence_records"])
