import type { AgentCommandKind, CommandRiskLevel, TaskCommandKind } from "../../lib/api/types";

export const COMMAND_CAPABILITY_BY_KIND: Record<AgentCommandKind, string> = {
  ping: "command.ping",
  restart: "command.restart",
  drain: "command.drain",
  pause_task: "command.pause_task",
  resume_task: "command.resume_task",
  discard_dead_letters: "command.discard_dead_letters",
  replay_dead_letters: "command.replay_dead_letters",
  run_task_once: "command.run_task_once",
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
  "run_task_once",
]);

const CRITICAL_COMMAND_KINDS = new Set<AgentCommandKind | TaskCommandKind>([
  "shutdown",
  "restart",
  "discard_dead_letters",
]);

const ELEVATED_COMMAND_KINDS = new Set<AgentCommandKind | TaskCommandKind>([
  "drain",
  "pause_task",
  "resume_task",
  "replay_dead_letters",
  "run_task_once",
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

export function isDestructiveCommand(kind: AgentCommandKind | TaskCommandKind) {
  return CRITICAL_COMMAND_KINDS.has(kind) || ELEVATED_COMMAND_KINDS.has(kind);
}

export function getCommandRiskLevel(kind: AgentCommandKind | TaskCommandKind): CommandRiskLevel {
  if (CRITICAL_COMMAND_KINDS.has(kind)) {
    return "critical";
  }
  return "elevated";
}
