"""Bounded, on-demand batch research refreshes for an explicit watchlist."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .narrative_provider import NarrativeMode
from .research_jobs import ResearchDataProvider, ResearchJob, ResearchJobRequest
from .watchlist import JsonWatchlistStore


class WatchlistRefreshRequest(BaseModel):
    """Shared bounded settings for a user-triggered watchlist refresh."""

    model_config = ConfigDict(frozen=True)

    as_of_date: date = Field(default_factory=date.today)
    lookback_days: int = Field(default=90, ge=1, le=3650)
    data_provider: ResearchDataProvider = ResearchDataProvider.AUTO
    narrative_mode: NarrativeMode = NarrativeMode.DETERMINISTIC


class WatchlistRefreshBatch(BaseModel):
    """The individual jobs created by one explicit batch request."""

    model_config = ConfigDict(frozen=True)

    batch_id: str
    symbols: list[str]
    jobs: list[ResearchJob]


class ResearchJobSubmitter(Protocol):
    def submit(self, request: ResearchJobRequest) -> ResearchJob:
        """Queue one normal local research job."""


def submit_watchlist_refresh(
    watchlist: JsonWatchlistStore,
    jobs: ResearchJobSubmitter,
    request: WatchlistRefreshRequest,
) -> WatchlistRefreshBatch:
    """Queue one ordinary research job per explicit watchlist entry.

    The existing local runner owns ordering and concurrency. Batch refreshes do
    not carry manual signals, so they cannot create a trade decision by default.
    """

    symbols = [entry.symbol for entry in watchlist.list_entries()]
    queued = [
        jobs.submit(
            ResearchJobRequest(
                symbol=symbol,
                as_of_date=request.as_of_date,
                lookback_days=request.lookback_days,
                data_provider=request.data_provider,
                narrative_mode=request.narrative_mode,
            )
        )
        for symbol in symbols
    ]
    return WatchlistRefreshBatch(batch_id=uuid4().hex, symbols=symbols, jobs=queued)
