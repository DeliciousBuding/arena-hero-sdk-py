"""Determinism and hygiene tests for generated arena.agent.io.v1 artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

from arena_hero.agent.io.v1 import MESSAGE_TYPES

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"
SCHEMA_PATH = GENERATED / "agent-io" / "arena.agent.io.v1.schema.json"
MANIFEST_PATH = GENERATED / "agent-io" / "schema-manifest.json"
TS_PATH = GENERATED / "typescript" / "agent-io-v1.ts"

# Locked hash of the committed canonical schema; update deliberately on contract change.
LOCKED_SCHEMA_HASH = "436e449d6f0790bb78b1501b12be3efb26c4c709c75725fed3bede9098bf5ccc"


def _load_generator() -> Any:
    path = REPO_ROOT / "scripts" / "generate_agent_io.py"
    spec = importlib.util.spec_from_file_location("arena_agent_io_generator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_schema_hash_is_locked() -> None:
    generator = _load_generator()
    schema = generator.build_schema()
    assert _sha256(generator.canonical_bytes(schema)) == LOCKED_SCHEMA_HASH


def test_committed_artifacts_match_fresh_generation() -> None:
    generator = _load_generator()
    schema = generator.build_schema()
    schema_data = generator.canonical_bytes(schema)
    schema_hash = generator.sha256_hex(schema_data)
    manifest = generator.canonical_bytes(generator.render_manifest(schema_hash))
    ts_text = generator.render_ts(schema, schema_hash).encode("utf-8")
    assert SCHEMA_PATH.read_bytes() == schema_data
    assert MANIFEST_PATH.read_bytes() == manifest
    assert TS_PATH.read_bytes() == ts_text


def test_regeneration_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = _load_generator()
    monkeypatch.setattr(generator, "SCHEMA_DIR", tmp_path / "agent-io")
    monkeypatch.setattr(generator, "TS_DIR", tmp_path / "typescript")
    assert generator.generate(check=False) == 0
    for committed, fresh in [
        (SCHEMA_PATH, tmp_path / "agent-io" / "arena.agent.io.v1.schema.json"),
        (MANIFEST_PATH, tmp_path / "agent-io" / "schema-manifest.json"),
        (TS_PATH, tmp_path / "typescript" / "agent-io-v1.ts"),
    ]:
        assert committed.read_bytes() == fresh.read_bytes()


def test_check_mode_reports_clean() -> None:
    generator = _load_generator()
    assert generator.main(["--check"]) == 0


def test_manifest_contract_metadata() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert payload["contract"] == "arena.agent.io.v1"
    assert payload["schemaVersion"] == 1
    assert payload["sha256"] == LOCKED_SCHEMA_HASH
    assert payload["messageTypes"] == list(MESSAGE_TYPES)
    assert payload["schemaFile"] == "arena.agent.io.v1.schema.json"
    assert payload["typescriptFile"] == "../typescript/agent-io-v1.ts"


def test_ts_helpers_present() -> None:
    text = TS_PATH.read_text(encoding="utf-8")
    assert "export type AgentMessage =" in text
    assert (
        "export function isAgentMessage(value: unknown): value is AgentMessage" in text
    )
    assert "export function parseAgentMessage(raw: string): AgentMessage" in text
    assert "export function encodeAgentMessage(message: AgentMessage): string" in text


def test_ts_has_no_any_or_unknown_escape() -> None:
    text = TS_PATH.read_text(encoding="utf-8")
    assert re.search(r"\bany\b", text) is None
    without_guard_params = text.replace("value: unknown", "")
    assert "unknown" not in without_guard_params


def test_ts_header_locks_schema_hash() -> None:
    text = TS_PATH.read_text(encoding="utf-8")
    assert f"Schema SHA-256: {LOCKED_SCHEMA_HASH}" in text
    assert "Do not edit by hand" in text


def test_generated_files_have_no_absolute_paths_or_secrets() -> None:
    for path in (SCHEMA_PATH, MANIFEST_PATH, TS_PATH):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", text) is None, path
        assert re.search(r"/Users/|/home/|/mnt/", text) is None, path
        assert "sk-" not in text, path
        assert "BEGIN PRIVATE KEY" not in text, path
