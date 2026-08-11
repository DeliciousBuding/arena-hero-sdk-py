"""Generate deterministic JSON Schema and TypeScript types for arena.agent.io.v1.

The Pydantic models in arena_hero.agent.io.v1 are the single source of truth.
This script exports them to generated/agent-io/ and generated/typescript/.
Running with --check verifies the on-disk artifacts are byte-identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from arena_hero.agent.io.v1 import (
    AGENT_IO_SCHEMA_VERSION,
    MESSAGE_TYPES,
    AgentMessage,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "generated" / "agent-io"
TS_DIR = REPO_ROOT / "generated" / "typescript"
SCHEMA_FILENAME = "arena.agent.io.v1.schema.json"
MANIFEST_FILENAME = "schema-manifest.json"
TS_FILENAME = "agent-io-v1.ts"
CONTRACT = "arena.agent.io.v1"
GENERATOR = "scripts/generate_agent_io.py"

SchemaNode = dict[str, Any]


def build_schema() -> SchemaNode:
    """Build the canonical JSON Schema for the arena.agent.io.v1 union."""

    schema = TypeAdapter(AgentMessage).json_schema(by_alias=True)
    for definition in schema.get("$defs", {}).values():
        for prop in definition.get("properties", {}).values():
            prop.pop("title", None)
    schema["$id"] = "https://doc.arenahero.io/schema/arena.agent.io.v1.schema.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = CONTRACT
    return schema


def canonical_bytes(schema: SchemaNode) -> bytes:
    """Serialize a schema into deterministic UTF-8 JSON bytes."""

    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True).encode()


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of the given bytes."""

    return hashlib.sha256(data).hexdigest()


def render_manifest(schema_hash: str) -> SchemaNode:
    """Build the deterministic artifact manifest (no timestamps)."""

    return {
        "contract": CONTRACT,
        "schemaVersion": AGENT_IO_SCHEMA_VERSION,
        "schemaFile": SCHEMA_FILENAME,
        "typescriptFile": f"../typescript/{TS_FILENAME}",
        "sha256": schema_hash,
        "messageTypes": list(MESSAGE_TYPES),
        "generator": GENERATOR,
    }


def ref_name(ref: str) -> str:
    """Strip a $ref like '#/$defs/HelloMessage' to its type name."""

    return ref.rsplit("/", 1)[-1]


def ts_type(node: SchemaNode) -> str:
    """Map one JSON Schema node to a TypeScript type expression."""

    if "$ref" in node:
        return ref_name(node["$ref"])
    if "const" in node:
        value = node["const"]
        return json.dumps(value) if isinstance(value, str) else str(value)
    if "enum" in node:
        return " | ".join(json.dumps(value) for value in node["enum"])
    if "anyOf" in node or "oneOf" in node:
        choices = node.get("anyOf") or node.get("oneOf")
        if choices is None:
            raise ValueError("union schema has no variants")
        return " | ".join(ts_type(choice) for choice in choices)
    kind = node.get("type")
    if kind == "object":
        additional = node.get("additionalProperties")
        if isinstance(additional, Mapping):
            return f"Record<string, {ts_type(additional)}>"
        return "JsonObject"
    if kind == "array":
        items = node.get("items")
        inner = ts_type(items) if isinstance(items, Mapping) else "JsonValue"
        return f"{inner}[]"
    if kind == "string":
        return "string"
    if kind == "integer":
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    return "JsonValue"


def ts_interface(name: str, node: SchemaNode) -> str:
    """Render one object schema definition as a TypeScript interface."""

    properties = node.get("properties", {})
    required = set(node.get("required", []))
    lines = [f"export interface {name} {{"]
    for prop in sorted(properties):
        optional = "" if prop in required else "?"
        lines.append(f"  {prop}{optional}: {ts_type(properties[prop])};")
    lines.append("}")
    return "\n".join(lines)


def is_required_nullable(node: SchemaNode) -> bool:
    """Return True when the node accepts null (anyOf/oneOf contains null)."""

    for key in ("anyOf", "oneOf"):
        for choice in node.get(key, []):
            if choice.get("type") == "null":
                return True
    return False


def guard_checks(name: str, node: SchemaNode) -> list[str]:
    """Render the runtime checks for one message type guard."""

    checks: list[str] = []
    properties = node.get("properties", {})
    required = set(node.get("required", []))
    discriminator = properties.get("type")
    if discriminator is not None and "const" in discriminator:
        expected_type = json.dumps(discriminator["const"])
        checks.append(f'  if (value["type"] !== {expected_type}) return false;')
    for prop in sorted(properties):
        if prop == "type" or prop not in required:
            continue
        prop_node = properties[prop]
        if is_required_nullable(prop_node):
            continue
        if "$ref" in prop_node:
            guard_name = ref_name(prop_node["$ref"])
            checks.append(f'  if (!is{guard_name}(value["{prop}"])) return false;')
        elif "const" in prop_node:
            value = prop_node["const"]
            expected = json.dumps(value) if isinstance(value, str) else str(value)
            checks.append(f'  if (value["{prop}"] !== {expected}) return false;')
        elif prop_node.get("type") == "string":
            checks.append(f'  if (typeof value["{prop}"] !== "string") return false;')
        elif prop_node.get("type") == "integer":
            checks.append(f'  if (typeof value["{prop}"] !== "number") return false;')
        elif prop_node.get("type") == "boolean":
            checks.append(f'  if (typeof value["{prop}"] !== "boolean") return false;')
        elif prop_node.get("type") == "object":
            checks.append(f'  if (!isRecord(value["{prop}"])) return false;')
        elif prop_node.get("type") == "array":
            checks.append(f'  if (!Array.isArray(value["{prop}"])) return false;')
    return checks


def render_ts(schema: SchemaNode, schema_hash: str) -> str:
    """Render the complete generated TypeScript module from the JSON Schema."""

    definitions = schema.get("$defs", {})
    root_one_of = schema.get("oneOf", [])
    lines: list[str] = []
    lines.append("/**")
    lines.append(" * Generated file. Do not edit by hand.")
    lines.append(f" * Contract: {CONTRACT}")
    lines.append(f" * Schema: generated/agent-io/{SCHEMA_FILENAME}")
    lines.append(f" * Schema SHA-256: {schema_hash}")
    lines.append(f" * Generate: python {GENERATOR}")
    lines.append(" */")
    lines.append("")
    lines.append("export type SchemaVersion = 1;")
    lines.append(
        "export type JsonValue = string | number | boolean | null | "
        "JsonObject | JsonValue[];"
    )
    lines.append("export interface JsonObject { [key: string]: JsonValue; }")
    lines.append("")
    for name in sorted(definitions):
        lines.append(ts_interface(name, definitions[name]))
        lines.append("")
    union_names = [ref_name(ref["$ref"]) for ref in root_one_of if "$ref" in ref]
    lines.append(f"export type AgentMessage = {' | '.join(union_names)};")
    lines.append("")
    lines.append("// -- runtime guards --")
    lines.append("")
    lines.append(
        "function isRecord(value: unknown): value is { [key: string]: JsonValue } {"
    )
    lines.append(
        '  return typeof value === "object" && value !== null && !Array.isArray(value);'
    )
    lines.append("}")
    lines.append("")
    for name in sorted(definitions):
        lines.append(f"function is{name}(value: unknown): value is {name} {{")
        lines.append("  if (!isRecord(value)) return false;")
        lines.extend(guard_checks(name, definitions[name]))
        lines.append("  return true;")
        lines.append("}")
        lines.append("")
    lines.append(
        "export function isAgentMessage(value: unknown): value is AgentMessage {"
    )
    lines.append("  if (!isRecord(value)) return false;")
    discriminators = (
        (name, json.dumps(definitions[name]["properties"]["type"]["const"]))
        for name in union_names
    )
    lines.extend(
        f'  if (value["type"] === {discriminator}) return is{name}(value);'
        for name, discriminator in discriminators
    )
    lines.append("  return false;")
    lines.append("}")
    lines.append("")
    lines.append("export function parseAgentMessage(raw: string): AgentMessage {")
    lines.append("  let value: unknown;")
    lines.append("  try {")
    lines.append("    value = JSON.parse(raw);")
    lines.append("  } catch {")
    lines.append('    throw new Error("invalid arena.agent.io.v1 message");')
    lines.append("  }")
    lines.append("  if (!isAgentMessage(value)) {")
    lines.append('    throw new Error("invalid arena.agent.io.v1 message");')
    lines.append("  }")
    lines.append("  return value;")
    lines.append("}")
    lines.append("")
    lines.append("export function encodeAgentMessage(message: AgentMessage): string {")
    lines.append("  return JSON.stringify(message);")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def generate(check: bool) -> int:
    """Write (or verify) the generated schema, manifest, and TS artifacts."""

    schema = build_schema()
    schema_data = canonical_bytes(schema)
    schema_hash = sha256_hex(schema_data)
    manifest = render_manifest(schema_hash)
    ts_text = render_ts(schema, schema_hash)
    artifacts: list[tuple[Path, bytes]] = [
        (SCHEMA_DIR / SCHEMA_FILENAME, schema_data),
        (SCHEMA_DIR / MANIFEST_FILENAME, canonical_bytes(manifest)),
        (TS_DIR / TS_FILENAME, ts_text.encode("utf-8")),
    ]
    mismatches: list[Path] = []
    for path, data in artifacts:
        if check:
            if path.read_bytes() != data:
                mismatches.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    for path in mismatches:
        print(f"stale: {path.relative_to(REPO_ROOT).as_posix()}")
    if check:
        print(
            "check: artifacts up to date"
            if not mismatches
            else "check: artifacts stale"
        )
        return 1 if mismatches else 0
    print(f"wrote: {len(artifacts)} artifacts (schema sha256 {schema_hash})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="Generate arena.agent.io.v1 artifacts")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated artifacts are byte-identical without writing",
    )
    args = parser.parse_args(argv)
    return generate(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
