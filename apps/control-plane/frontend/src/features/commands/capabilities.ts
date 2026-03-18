import type { AgentCommandKind } from "../../lib/api/types";

export const COMMAND_CAPABILITY_BY_KIND: Record<AgentCommandKind, string> = {
  ping: "command.ping",
  restart: "command.restart",
  drain: "command.drain",
  pause_task: "command.pause_task",
  resume_task: "command.resume_task",
  discard_dead_letters: "command.discard_dead_letters",
  replay_dead_letters: "command.replay_dead_letters",
  sync_now: "command.sync_now",
  flush_metrics: "command.flush_metrics",
  flush_events: "command.flush_events",
  shutdown: "command.shutdown",
};

const QUEUEABLE_COMMAND_KINDS = new Set<AgentCommandKind>([
  "ping",
  "sync_now",
  "flush_metrics",
  "flush_events",
]);

const REASON_REQUIRED_COMMAND_KINDS = new Set<AgentCommandKind>([
  "shutdown",
  "restart",
  "drain",
  "pause_task",
  "resume_task",
  "discard_dead_letters",
  "replay_dead_letters",
]);

export function getCommandCapability(kind: AgentCommandKind): string {
  return COMMAND_CAPABILITY_BY_KIND[kind];
}

export function hasCommandCapability(
  kind: AgentCommandKind,
  acceptedCapabilities: string[],
): boolean {
  return acceptedCapabilities.includes(getCommandCapability(kind));
}

export function commandSupportsQueueing(kind: AgentCommandKind): boolean {
  return QUEUEABLE_COMMAND_KINDS.has(kind);
}

export function commandRequiresReason(kind: AgentCommandKind): boolean {
  return REASON_REQUIRED_COMMAND_KINDS.has(kind);
}
