"""Small local watchlist store for the personal research cockpit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WatchlistEntry(BaseModel):
    """One ticker explicitly followed by the local user."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return normalized


class JsonWatchlistStore:
    """Local JSON watchlist colocated with a research artifact cache."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_entries(self) -> list[WatchlistEntry]:
        if not self.path.exists():
            return []
        try:
            entries = [WatchlistEntry.model_validate(item) for item in self._read_payload()]
        except (OSError, ValueError):
            return []
        return sorted(entries, key=lambda entry: entry.symbol)

    def add(self, symbol: str) -> WatchlistEntry:
        entry = WatchlistEntry(symbol=symbol)
        entries = {item.symbol: item for item in self.list_entries()}
        entries.setdefault(entry.symbol, entry)
        self._write(list(entries.values()))
        return entries[entry.symbol]

    def remove(self, symbol: str) -> bool:
        normalized = WatchlistEntry(symbol=symbol).symbol
        entries = self.list_entries()
        kept = [entry for entry in entries if entry.symbol != normalized]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True

    @property
    def path(self) -> Path:
        return self.root / "watchlist.json"

    def _read_payload(self) -> list[object]:
        payload = self.path.read_text(encoding="utf-8")
        from json import loads

        parsed = loads(payload)
        if not isinstance(parsed, list):
            raise ValueError("watchlist payload must be a list")
        return parsed

    def _write(self, entries: list[WatchlistEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "[\n" + ",\n".join(entry.model_dump_json(indent=2) for entry in entries) + "\n]\n"
        self.path.write_text(payload, encoding="utf-8")
