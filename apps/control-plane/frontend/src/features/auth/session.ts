import type { ConsoleRole, ConsoleSessionResponse } from "../../lib/api/types";

export function getConsoleRole(session: ConsoleSessionResponse | null | undefined): ConsoleRole | null {
  if (!session) {
    return null;
  }
  if (session.role) {
    return session.role;
  }
  return session.roles[0] ?? null;
}

export function canViewCommandControls(session: ConsoleSessionResponse | null | undefined) {
  const role = getConsoleRole(session);
  if (role === null) {
    return true;
  }
  return role === "operator" || role === "admin";
}

export function canManageNotificationSettings(session: ConsoleSessionResponse | null | undefined) {
  return canViewCommandControls(session);
}

export function canViewDestructiveControls(session: ConsoleSessionResponse | null | undefined) {
  const role = getConsoleRole(session);
  if (role === null) {
    return true;
  }
  return role === "admin";
}
