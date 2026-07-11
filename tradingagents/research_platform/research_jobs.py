"""Controlled local background jobs for the personal research workflow."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .artifact_store import JsonArtifactStore
from .data_contracts import DataProvider
from .research_workflow import ResearchWorkflowConfig, run_ticker_research
from .run_archive import JsonResearchRunArchive
from .yfinance_provider import YFinanceProvider


class ResearchJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ResearchJobRequest(BaseModel):
    """Bounded input accepted by the local cockpit research launcher."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    as_of_date: date = Field(default_factory=date.today)
    lookback_days: int = Field(default=90, ge=1, le=3650)
    currency: str | None = None

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return normalized


class ResearchJob(BaseModel):
    """Status record for one local research execution."""

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(min_length=1)
    request: ResearchJobRequest
    status: ResearchJobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    report_path: Path | None = None
    run_id: str | None = None
    error: str | None = None


ProviderFactory = Callable[[ResearchJobRequest], DataProvider]


class LocalResearchJobRunner:
    """Single-worker runner for a local research directory.

    The runner deliberately executes one task at a time. This avoids duplicate
    yfinance requests and keeps cache/report writes predictable for a single
    personal workstation. Completed research is persisted by the workflow;
    the in-memory job list is only a live status surface for the cockpit.
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        provider_factory: ProviderFactory | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.provider_factory = provider_factory or self._default_provider_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-job")
        self._jobs: dict[str, ResearchJob] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = Lock()

    def submit(self, request: ResearchJobRequest) -> ResearchJob:
        """Queue one bounded local research run."""

        now = _utc_now()
        job = ResearchJob(
            job_id=uuid4().hex,
            request=request,
            status=ResearchJobStatus.QUEUED,
            created_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._futures[job.job_id] = self._executor.submit(self._execute, job.job_id)
        return job

    def get(self, job_id: str) -> ResearchJob | None:
        """Return a job status record when it belongs to this server process."""

        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[ResearchJob]:
        """List newest jobs first for the local cockpit."""

        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def wait(self, job_id: str, timeout: float | None = None) -> ResearchJob:
        """Wait for a job in tests or local scripts, then return its final state."""

        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            raise KeyError(job_id)
        future.result(timeout=timeout)
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def shutdown(self) -> None:
        """Release the worker used by this local server."""

        self._executor.shutdown(wait=False, cancel_futures=False)

    def _execute(self, job_id: str) -> None:
        self._update(job_id, status=ResearchJobStatus.RUNNING, started_at=_utc_now())
        job = self.get(job_id)
        if job is None:
            return
        try:
            provider = self.provider_factory(job.request)
            result = run_ticker_research(
                config=ResearchWorkflowConfig(
                    symbol=job.request.symbol,
                    as_of_date=job.request.as_of_date,
                    lookback_days=job.request.lookback_days,
                    currency=job.request.currency,
                ),
                provider=provider,
                store=JsonArtifactStore(self.data_dir),
                archive=JsonResearchRunArchive(self.data_dir),
                output_dir=self.data_dir / "reports",
            )
        except Exception as error:  # Provider errors become visible job state, not server crashes.
            self._update(
                job_id,
                status=ResearchJobStatus.FAILED,
                completed_at=_utc_now(),
                error=str(error) or error.__class__.__name__,
            )
            return

        self._update(
            job_id,
            status=ResearchJobStatus.SUCCEEDED,
            completed_at=_utc_now(),
            report_path=result.report_path,
            run_id=result.archived_run.run_id if result.archived_run is not None else None,
        )

    def _update(self, job_id: str, **updates: object) -> None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                self._jobs[job_id] = current.model_copy(update=updates)

    def _default_provider_factory(self, request: ResearchJobRequest) -> DataProvider:
        cache_dir = self.data_dir / "yfinance"
        return YFinanceProvider(cache_dir=cache_dir)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
