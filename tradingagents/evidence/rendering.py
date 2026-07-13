"""Human-readable citation and source rendering."""

from __future__ import annotations

from collections.abc import Iterable

from .models import ClaimRecord, EvidenceRecord


def render_claim_text(text: str, claim: ClaimRecord | None = None) -> str:
    """Render a claim with citations or an explicit support warning."""
    if claim is None:
        return text
    citations = " ".join(f"[{citation_id}]" for citation_id in claim.citation_ids)
    if claim.support_status == "unsupported":
        suffix = " **[Unsupported claim]**"
    elif claim.support_status == "unusable":
        suffix = " **[Citations unusable for the analysis date]**"
    elif claim.support_status == "conflicting":
        suffix = " **[Conflicting sources]**"
    else:
        suffix = ""
    return " ".join(part for part in (text, citations, suffix) if part).strip()


def render_sources(records: Iterable[EvidenceRecord]) -> str:
    """Render a deduplicated Sources section."""
    sources: dict[tuple[str, str], EvidenceRecord] = {}
    for record in records:
        key = (record.source_url or "", record.source_title.casefold())
        sources.setdefault(key, record)
    if not sources:
        return ""

    lines = ["## Sources", ""]
    for record in sorted(sources.values(), key=lambda item: item.evidence_id):
        dates = []
        if record.filing_date:
            dates.append(f"filed {record.filing_date.isoformat()}")
        if record.publication_date and record.publication_date != record.filing_date:
            dates.append(f"published {record.publication_date.isoformat()}")
        date_text = f"; {', '.join(dates)}" if dates else ""
        primary = "primary" if record.is_primary_source else "secondary/structured provider"
        temporal = "date-valid" if record.analysis_date_valid else "temporally unusable"
        title = record.source_title.replace("\n", " ").strip()
        if record.source_url:
            source = f"[{title}]({record.source_url})"
        else:
            source = f"{title} (URL unavailable)"
        lines.append(
            f"- **{record.evidence_id}** — {source}; "
            f"{record.publisher or 'publisher unavailable'}; {primary}; "
            f"{temporal}{date_text}."
        )
    return "\n".join(lines)


def append_sources(report: str, records: Iterable[EvidenceRecord]) -> str:
    """Append Sources when records exist, preserving legacy prose otherwise."""
    sources = render_sources(records)
    if not sources:
        return report
    return f"{report.rstrip()}\n\n{sources}"
