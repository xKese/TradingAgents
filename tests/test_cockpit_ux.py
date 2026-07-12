import re

from tradingagents.research_platform.cockpit import _APP_HTML


def test_cockpit_uses_five_task_focused_views():
    tabs = re.findall(r'data-view-target="([^"]+)"', _APP_HTML)
    views = re.findall(r'data-view="([^"]+)"', _APP_HTML)

    assert tabs == ["overview", "game", "financials", "research", "decision"]
    assert views == tabs
    assert 'id="view-overview" role="tabpanel" data-view="overview"' in _APP_HTML
    assert 'id="view-game" role="tabpanel" data-view="game" hidden' in _APP_HTML


def test_cockpit_has_clear_primary_and_secondary_actions():
    assert '>研究当前股票</button>' in _APP_HTML
    assert '>更新全部自选股</button>' in _APP_HTML
    assert '<summary class="menu-summary">自选管理</summary>' in _APP_HTML
    assert '>读取本地数据</button>' in _APP_HTML
    assert "refreshWatchlistResearch').addEventListener('click'" in _APP_HTML


def test_long_research_details_start_collapsed():
    assert '<details id="reportDisclosure" class="report-disclosure">' in _APP_HTML
    assert '<details class="panel span-2 readiness-disclosure">' in _APP_HTML
    assert '<details id="reportDisclosure" class="report-disclosure" open>' not in _APP_HTML
    assert "展开后加载报告全文。" in _APP_HTML


def test_render_targets_remain_unique_after_view_reorganization():
    ids = re.findall(r'id="([^"]+)"', _APP_HTML)
    required = {
        "metrics",
        "dataHealth",
        "readiness",
        "watchlistBoard",
        "gameOpportunity",
        "gameProducts",
        "fundamentals",
        "reportPreview",
        "decision",
        "decisionJournal",
        "backtest",
    }

    assert required.issubset(ids)
    assert all(ids.count(item) == 1 for item in required)
