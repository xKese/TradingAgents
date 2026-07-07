"""Unit tests for the Form 4 ownership-XML parser."""

from datetime import date
from decimal import Decimal

import pytest

from tradingagents.dataflows import form4
from tradingagents.dataflows.edgar import Filing

pytestmark = pytest.mark.unit

FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <aff10b5One>0</aff10b5One>
    <issuer><issuerTradingSymbol>WIDG</issuerTradingSymbol></issuer>
    <reportingOwner>
        <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>1</isDirector>
            <isOfficer>1</isOfficer>
            <officerTitle>CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-06-15</value></transactionDate>
            <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
            <transactionAmounts>
                <transactionShares><value>10000</value></transactionShares>
                <transactionPricePerShare><value>4.25</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-06-16</value></transactionDate>
            <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
            <transactionAmounts>
                <transactionShares><value>2000</value></transactionShares>
                <transactionPricePerShare><value>4.60</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""

FORM4_10B51_GRANT = FORM4_XML.replace(
    "<aff10b5One>0</aff10b5One>", "<aff10b5One>1</aff10b5One>",
).replace(
    "<transactionCode>P</transactionCode>", "<transactionCode>A</transactionCode>",
)

ACCESSION = "0000000001-26-000042"
FILED = date(2026, 6, 17)


def test_parses_buy_and_sale_with_identity():
    txns = form4.parse_form4_xml(FORM4_XML, accession=ACCESSION, filed_date=FILED)
    assert len(txns) == 2
    buy, sale = txns
    assert buy.insider_name == "DOE JANE"
    assert buy.is_officer and buy.is_director and not buy.is_ten_pct_owner
    assert buy.insider_title == "CEO"
    assert buy.code == "P" and buy.kind == "open_market_buy"
    assert buy.shares == Decimal("10000")
    assert buy.price == Decimal("4.25")
    assert buy.acquired is True
    assert buy.ten_b5_1 is False
    assert buy.transaction_date == date(2026, 6, 15)
    assert buy.accession == ACCESSION
    assert sale.code == "S" and sale.kind == "open_market_sale"
    assert sale.acquired is False


def test_10b51_flag_and_grant_kind():
    txns = form4.parse_form4_xml(FORM4_10B51_GRANT, accession=ACCESSION, filed_date=FILED)
    assert all(t.ten_b5_1 for t in txns)
    assert txns[0].kind == "grant"


def test_malformed_xml_returns_empty():
    assert form4.parse_form4_xml("<not-xml", accession=ACCESSION, filed_date=FILED) == []


def test_raw_xml_url_strips_xsl_prefix():
    f = Filing(
        ticker="WIDG", cik=1234567, accession_number=ACCESSION, form="4",
        filing_date=FILED, report_date=None,
        primary_document="xslF345X05/wk-form4_123.xml",
    )
    url = form4.raw_xml_url(f)
    assert "xslF345X05" not in url
    assert url.endswith("/wk-form4_123.xml")


def test_get_insider_transactions_lists_and_parses():
    f = Filing(
        ticker="WIDG", cik=1234567, accession_number=ACCESSION, form="4",
        filing_date=FILED, report_date=None, primary_document="form4.xml",
    )
    txns = form4.get_insider_transactions(
        "WIDG", since=date(2026, 4, 1),
        list_filings=lambda ticker, **kw: [f],
        fetch_raw=lambda url: FORM4_XML,
    )
    assert len(txns) == 2
    assert txns[0].filed_date == FILED


def test_get_insider_transactions_caps_filings():
    filings = [
        Filing(
            ticker="WIDG", cik=1234567, accession_number=f"000-26-{i:06d}", form="4",
            filing_date=date(2026, 6, 17), report_date=None, primary_document="form4.xml",
        )
        for i in range(20)
    ]
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return FORM4_XML

    form4.get_insider_transactions(
        "WIDG", since=date(2026, 4, 1), max_filings=3,
        list_filings=lambda ticker, **kw: filings, fetch_raw=fake_fetch,
    )
    assert len(fetched) == 3
