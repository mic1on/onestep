import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AgentCommandKind } from "../../../lib/api/types";

type CommandQuickActionsProps = {
  disabled: boolean;
  isSubmitting: boolean;
  onSubmit: (kind: AgentCommandKind) => Promise<void>;
};

const COMMAND_ACTIONS: AgentCommandKind[] = [
  "ping",
  "sync_now",
  "flush_metrics",
  "flush_events",
  "shutdown",
];

export function CommandQuickActions({
  disabled,
  isSubmitting,
  onSubmit,
}: CommandQuickActionsProps) {
  const { t } = useTranslation();
  const [pendingKind, setPendingKind] = useState<AgentCommandKind | null>(null);

  async function handleSubmit(kind: AgentCommandKind) {
    if (kind === "shutdown") {
      const confirmed = window.confirm(t("commands.shutdownConfirm"));
      if (!confirmed) {
        return;
      }
    }

    setPendingKind(kind);
    try {
      await onSubmit(kind);
    } catch {
      // The caller owns user-facing error state.
    } finally {
      setPendingKind(null);
    }
  }

  return (
    <div className="command-action-grid">
      {COMMAND_ACTIONS.map((kind) => {
        const isCurrentPending = isSubmitting && pendingKind === kind;
        return (
          <button
            key={kind}
            className={kind === "shutdown" ? "button-secondary button-danger" : "button-secondary"}
            disabled={disabled || isSubmitting}
            onClick={() => void handleSubmit(kind)}
            type="button"
          >
            {isCurrentPending
              ? t("commands.dispatchingAction", {
                  kind: t(`commandKind.${kind}`, { defaultValue: kind }),
                })
              : t(`commands.action.${kind}`)}
          </button>
        );
      })}
    </div>
  );
}
