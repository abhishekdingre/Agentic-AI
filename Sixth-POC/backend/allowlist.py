"""Loads and validates `data/allowlist.json` — the single source of truth for
which documents exist, their domain/version/status, and their supersession
chain. This module is commitment #1's foundation: `ingest.py` only embeds
`current` documents, `search_corpus`'s domain enum is built from the caller's
selection, and `grounding.py` re-checks every citation's source_id against
`approved_current_ids()` loaded fresh here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from backend.config import ALLOWLIST_PATH

VALID_DOMAINS = {"literature", "patents", "clinical_trials", "internal_reports"}
VALID_STATUSES = {"current", "superseded"}


@dataclass(frozen=True)
class AllowlistEntry:
    source_id: str
    domain: str
    title: str
    version: str
    status: str
    supersedes: str | None
    superseded_by: str | None
    file_path: str


class AllowlistError(ValueError):
    pass


def _validate_entry(raw: dict) -> AllowlistEntry:
    required = {
        "source_id",
        "domain",
        "title",
        "version",
        "status",
        "supersedes",
        "superseded_by",
        "file_path",
    }
    missing = required - raw.keys()
    if missing:
        raise AllowlistError(f"allowlist entry missing fields {missing}: {raw}")
    if raw["domain"] not in VALID_DOMAINS:
        raise AllowlistError(
            f"allowlist entry {raw.get('source_id')!r} has invalid domain {raw['domain']!r}"
        )
    if raw["status"] not in VALID_STATUSES:
        raise AllowlistError(
            f"allowlist entry {raw.get('source_id')!r} has invalid status {raw['status']!r}"
        )
    return AllowlistEntry(
        source_id=raw["source_id"],
        domain=raw["domain"],
        title=raw["title"],
        version=raw["version"],
        status=raw["status"],
        supersedes=raw["supersedes"],
        superseded_by=raw["superseded_by"],
        file_path=raw["file_path"],
    )


def load_allowlist(path=ALLOWLIST_PATH) -> list[AllowlistEntry]:
    """Load and validate the allowlist from disk (no caching — for tests/ingest
    that want a fresh read of a possibly-edited file)."""
    with open(path, encoding="utf-8") as f:
        raw_entries = json.load(f)
    if not isinstance(raw_entries, list):
        raise AllowlistError("allowlist.json must be a JSON array")

    entries = [_validate_entry(e) for e in raw_entries]

    ids = [e.source_id for e in entries]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise AllowlistError(f"duplicate source_id(s) in allowlist: {dupes}")

    id_set = set(ids)
    for e in entries:
        if e.supersedes and e.supersedes not in id_set:
            raise AllowlistError(
                f"{e.source_id}.supersedes references unknown source_id {e.supersedes!r}"
            )
        if e.superseded_by and e.superseded_by not in id_set:
            raise AllowlistError(
                f"{e.source_id}.superseded_by references unknown source_id {e.superseded_by!r}"
            )
        if e.status == "superseded" and not e.superseded_by:
            raise AllowlistError(
                f"{e.source_id} has status=superseded but no superseded_by"
            )
        if e.status == "current" and e.supersedes:
            superseder = next((x for x in entries if x.source_id == e.supersedes), None)
            if superseder is not None and superseder.status != "superseded":
                raise AllowlistError(
                    f"{e.source_id} supersedes {e.supersedes!r} but that entry isn't marked superseded"
                )

    return entries


@lru_cache(maxsize=1)
def _cached_allowlist() -> tuple[AllowlistEntry, ...]:
    return tuple(load_allowlist())


def all_entries() -> list[AllowlistEntry]:
    """Cached full allowlist (current + superseded)."""
    return list(_cached_allowlist())


def current() -> list[AllowlistEntry]:
    """Entries with status == 'current' — the only documents ingest.py embeds."""
    return [e for e in all_entries() if e.status == "current"]


def approved_current_ids() -> set[str]:
    """The set of source_ids that are currently approved for retrieval/citation."""
    return {e.source_id for e in current()}


def by_source_id(source_id: str) -> AllowlistEntry | None:
    for e in all_entries():
        if e.source_id == source_id:
            return e
    return None


def clear_cache() -> None:
    """Used by tests that swap in a different allowlist file."""
    _cached_allowlist.cache_clear()
