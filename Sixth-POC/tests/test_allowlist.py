"""Unit tests for backend/allowlist.py — no model calls, no network.

Covers both the real project allowlist (data/allowlist.json, including the
IR-002 version-drift trap) and synthetic temp-file allowlists that exercise
every validation error path.
"""
from __future__ import annotations

import json

import pytest

from backend import allowlist
from backend.config import ALLOWLIST_PATH


# --- Real allowlist.json -----------------------------------------------------


def test_real_allowlist_loads_and_has_28_entries():
    entries = allowlist.load_allowlist(ALLOWLIST_PATH)
    assert len(entries) == 28


def test_real_allowlist_current_vs_superseded_split():
    entries = allowlist.load_allowlist(ALLOWLIST_PATH)
    current = [e for e in entries if e.status == "current"]
    superseded = [e for e in entries if e.status == "superseded"]
    assert len(current) == 27
    assert len(superseded) == 1
    assert superseded[0].source_id == "IR-002-v1"


def test_ir002_version_drift_trap_wiring():
    entries = {e.source_id: e for e in allowlist.load_allowlist(ALLOWLIST_PATH)}
    v1 = entries["IR-002-v1"]
    v2 = entries["IR-002-v2"]
    assert v1.status == "superseded"
    assert v1.superseded_by == "IR-002-v2"
    assert v2.status == "current"
    assert v2.supersedes == "IR-002-v1"


def test_approved_current_ids_excludes_superseded():
    allowlist.clear_cache()
    ids = allowlist.approved_current_ids()
    assert "IR-002-v1" not in ids
    assert "IR-002-v2" in ids
    assert len(ids) == 27


def test_current_matches_approved_current_ids():
    allowlist.clear_cache()
    current_ids = {e.source_id for e in allowlist.current()}
    assert current_ids == allowlist.approved_current_ids()


def test_by_source_id_found_and_not_found():
    allowlist.clear_cache()
    entry = allowlist.by_source_id("LIT-001")
    assert entry is not None
    assert entry.domain == "literature"
    assert allowlist.by_source_id("NOT-A-REAL-ID") is None


def test_no_duplicate_source_ids_in_real_allowlist():
    entries = allowlist.load_allowlist(ALLOWLIST_PATH)
    ids = [e.source_id for e in entries]
    assert len(ids) == len(set(ids))


def test_all_domains_and_statuses_valid_in_real_allowlist():
    entries = allowlist.load_allowlist(ALLOWLIST_PATH)
    for e in entries:
        assert e.domain in allowlist.VALID_DOMAINS
        assert e.status in allowlist.VALID_STATUSES


# --- Synthetic allowlists: validation error paths ----------------------------


def _base_entry(**overrides) -> dict:
    entry = {
        "source_id": "TEST-001",
        "domain": "literature",
        "title": "Test Document",
        "version": "1.0",
        "status": "current",
        "supersedes": None,
        "superseded_by": None,
        "file_path": "data/corpus/literature/LIT-001.md",
    }
    entry.update(overrides)
    return entry


def _write_allowlist(tmp_path, entries: list[dict]):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_missing_required_field_raises(tmp_path):
    entry = _base_entry()
    del entry["title"]
    path = _write_allowlist(tmp_path, [entry])
    with pytest.raises(allowlist.AllowlistError, match="missing fields"):
        allowlist.load_allowlist(path)


def test_invalid_domain_raises(tmp_path):
    path = _write_allowlist(tmp_path, [_base_entry(domain="not_a_domain")])
    with pytest.raises(allowlist.AllowlistError, match="invalid domain"):
        allowlist.load_allowlist(path)


def test_invalid_status_raises(tmp_path):
    path = _write_allowlist(tmp_path, [_base_entry(status="draft")])
    with pytest.raises(allowlist.AllowlistError, match="invalid status"):
        allowlist.load_allowlist(path)


def test_duplicate_source_id_raises(tmp_path):
    path = _write_allowlist(tmp_path, [_base_entry(), _base_entry()])
    with pytest.raises(allowlist.AllowlistError, match="duplicate source_id"):
        allowlist.load_allowlist(path)


def test_dangling_supersedes_reference_raises(tmp_path):
    path = _write_allowlist(tmp_path, [_base_entry(supersedes="GHOST-001")])
    with pytest.raises(allowlist.AllowlistError, match="unknown source_id"):
        allowlist.load_allowlist(path)


def test_dangling_superseded_by_reference_raises(tmp_path):
    path = _write_allowlist(
        tmp_path, [_base_entry(status="superseded", superseded_by="GHOST-002")]
    )
    with pytest.raises(allowlist.AllowlistError, match="unknown source_id"):
        allowlist.load_allowlist(path)


def test_superseded_without_superseded_by_raises(tmp_path):
    path = _write_allowlist(tmp_path, [_base_entry(status="superseded", superseded_by=None)])
    with pytest.raises(allowlist.AllowlistError, match="no superseded_by"):
        allowlist.load_allowlist(path)


def test_current_supersedes_entry_that_is_not_marked_superseded_raises(tmp_path):
    entries = [
        _base_entry(source_id="NEW-001", status="current", supersedes="OLD-001"),
        _base_entry(source_id="OLD-001", status="current", supersedes=None, superseded_by=None),
    ]
    path = _write_allowlist(tmp_path, entries)
    with pytest.raises(allowlist.AllowlistError, match="isn't marked superseded"):
        allowlist.load_allowlist(path)


def test_valid_version_drift_pair_loads_cleanly(tmp_path):
    entries = [
        _base_entry(source_id="NEW-001", status="current", supersedes="OLD-001"),
        _base_entry(
            source_id="OLD-001",
            status="superseded",
            supersedes=None,
            superseded_by="NEW-001",
        ),
    ]
    path = _write_allowlist(tmp_path, entries)
    loaded = allowlist.load_allowlist(path)
    assert len(loaded) == 2


def test_not_a_json_array_raises(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(allowlist.AllowlistError, match="JSON array"):
        allowlist.load_allowlist(path)
