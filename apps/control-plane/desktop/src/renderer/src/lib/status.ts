import type { HealthStatus } from "../api/types";
import type { StatusTone } from "../components/ui/StatusDot";

export function healthTone(status: HealthStatus | string | null | undefined): StatusTone {
  if (status === "healthy" || status === "ok" || status === "running" || status === "connected" || status === "online") {
    return "success";
  }
  if (status === "degraded" || status === "pending" || status === "starting" || status === "attention") {
    return "warning";
  }
  if (status === "offline" || status === "failed" || status === "error" || status === "disconnected") {
    return "danger";
  }
  return "neutral";
}
