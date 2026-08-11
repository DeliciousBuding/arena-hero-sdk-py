/**
 * Generated file. Do not edit by hand.
 * Contract: arena.agent.io.v1
 * Schema: generated/agent-io/arena.agent.io.v1.schema.json
 * Schema SHA-256: 436e449d6f0790bb78b1501b12be3efb26c4c709c75725fed3bede9098bf5ccc
 * Generate: python scripts/generate_agent_io.py
 */

export type SchemaVersion = 1;
export type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
export interface JsonObject { [key: string]: JsonValue; }

export interface AgentCapabilities {
  cancel?: boolean;
}

export interface DecideMessage {
  deadlineMs?: number | null;
  messageId: string;
  payload?: JsonObject | null;
  requestId: string;
  schemaVersion: 1;
  tenantId: string;
  tick: number;
  type: "decide";
}

export interface DecisionMessage {
  decisionId: string;
  messageId: string;
  payload?: JsonObject | null;
  requestId: string;
  schemaVersion: 1;
  tenantId: string;
  tick: number;
  type: "decision";
}

export interface EpisodeEndMessage {
  messageId: string;
  outcome: "completed" | "aborted" | "timeout" | "invalidated";
  payload?: JsonObject | null;
  schemaVersion: 1;
  tenantId: string;
  type: "episode_end";
}

export interface EpisodeStartMessage {
  deadlineMs?: number | null;
  messageId: string;
  payload?: JsonObject | null;
  rulesVersion: string;
  schemaDigests?: Record<string, string> | null;
  schemaVersion: 1;
  seed?: number | null;
  tenantId: string;
  type: "episode_start";
}

export interface ErrorMessage {
  code: string;
  details?: JsonObject | null;
  message: string;
  messageId: string;
  requestId?: string | null;
  schemaVersion: 1;
  type: "error";
}

export interface HelloMessage {
  capabilities: AgentCapabilities;
  contestant: string;
  messageId: string;
  payload?: JsonObject | null;
  rulesVersions?: string[];
  schemaVersion: 1;
  type: "hello";
}

export interface ReadyMessage {
  messageId: string;
  note?: string | null;
  requestId: string;
  schemaVersion: 1;
  tenantId: string;
  type: "ready";
}

export type AgentMessage = HelloMessage | ReadyMessage | EpisodeStartMessage | DecideMessage | DecisionMessage | EpisodeEndMessage | ErrorMessage;

// -- runtime guards --

function isRecord(value: unknown): value is { [key: string]: JsonValue } {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAgentCapabilities(value: unknown): value is AgentCapabilities {
  if (!isRecord(value)) return false;
  return true;
}

function isDecideMessage(value: unknown): value is DecideMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "decide") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (typeof value["requestId"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  if (typeof value["tenantId"] !== "string") return false;
  if (typeof value["tick"] !== "number") return false;
  return true;
}

function isDecisionMessage(value: unknown): value is DecisionMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "decision") return false;
  if (typeof value["decisionId"] !== "string") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (typeof value["requestId"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  if (typeof value["tenantId"] !== "string") return false;
  if (typeof value["tick"] !== "number") return false;
  return true;
}

function isEpisodeEndMessage(value: unknown): value is EpisodeEndMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "episode_end") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (typeof value["outcome"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  if (typeof value["tenantId"] !== "string") return false;
  return true;
}

function isEpisodeStartMessage(value: unknown): value is EpisodeStartMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "episode_start") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (typeof value["rulesVersion"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  if (typeof value["tenantId"] !== "string") return false;
  return true;
}

function isErrorMessage(value: unknown): value is ErrorMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "error") return false;
  if (typeof value["code"] !== "string") return false;
  if (typeof value["message"] !== "string") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  return true;
}

function isHelloMessage(value: unknown): value is HelloMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "hello") return false;
  if (!isAgentCapabilities(value["capabilities"])) return false;
  if (typeof value["contestant"] !== "string") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  return true;
}

function isReadyMessage(value: unknown): value is ReadyMessage {
  if (!isRecord(value)) return false;
  if (value["type"] !== "ready") return false;
  if (typeof value["messageId"] !== "string") return false;
  if (typeof value["requestId"] !== "string") return false;
  if (value["schemaVersion"] !== 1) return false;
  if (typeof value["tenantId"] !== "string") return false;
  return true;
}

export function isAgentMessage(value: unknown): value is AgentMessage {
  if (!isRecord(value)) return false;
  if (value["type"] === "hello") return isHelloMessage(value);
  if (value["type"] === "ready") return isReadyMessage(value);
  if (value["type"] === "episode_start") return isEpisodeStartMessage(value);
  if (value["type"] === "decide") return isDecideMessage(value);
  if (value["type"] === "decision") return isDecisionMessage(value);
  if (value["type"] === "episode_end") return isEpisodeEndMessage(value);
  if (value["type"] === "error") return isErrorMessage(value);
  return false;
}

export function parseAgentMessage(raw: string): AgentMessage {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("invalid arena.agent.io.v1 message");
  }
  if (!isAgentMessage(value)) {
    throw new Error("invalid arena.agent.io.v1 message");
  }
  return value;
}

export function encodeAgentMessage(message: AgentMessage): string {
  return JSON.stringify(message);
}
