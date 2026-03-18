import { useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "../../../components/ui/SegmentedControl";
import type { AgentCommandDeliveryMode, AgentCommandKind } from "../../../lib/api/types";
import {
  commandSupportsQueueing,
  getCommandCapability,
  hasCommandCapability,
} from "../capabilities";

type CommandQuickActionsProps = {
  hasActiveSession: boolean;
  hasKnownSession: boolean;
  activeAcceptedCapabilities: string[];
  latestAcceptedCapabilities: string[];
  deliveryMode: AgentCommandDeliveryMode;
  isSubmitting: boolean;
  onDeliveryModeChange: (mode: AgentCommandDeliveryMode) => void;
  onSubmit: (kind: AgentCommandKind) => Promise<void>;
};

const COMMAND_ACTIONS: AgentCommandKind[] = [
  "ping",
  "sync_now",
  "flush_metrics",
  "flush_events",
  "drain",
  "restart",
  "shutdown",
];

export function CommandQuickActions({
  hasActiveSession,
  hasKnownSession,
  activeAcceptedCapabilities,
  latestAcceptedCapabilities,
  deliveryMode,
  isSubmitting,
  onDeliveryModeChange,
  onSubmit,
}: CommandQuickActionsProps) {
  const { t } = useTranslation();
  const [pendingKind, setPendingKind] = useState<AgentCommandKind | null>(null);
  const isQueueMode = deliveryMode === "queue_until_reconnect";
  const sessionBlockReason =
    deliveryMode === "dispatch_now_only"
      ? hasActiveSession
        ? null
        : t("commands.disabledReason.noSession")
      : hasKnownSession
        ? null
        : t("commands.disabledReason.noKnownSession");

  async function handleSubmit(kind: AgentCommandKind) {
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
    <div className="command-actions-stack">
      <div className="command-delivery-mode-section">
        <span className="list-row-label">{t("commands.deliveryMode.label")}</span>
        <SegmentedControl
          ariaLabel={t("commands.deliveryMode.ariaLabel")}
          onChange={(value) => onDeliveryModeChange(value as AgentCommandDeliveryMode)}
          options={[
            {
              label: t("commands.deliveryMode.dispatchNowOnly"),
              value: "dispatch_now_only",
            },
            {
              label: t("commands.deliveryMode.queueUntilReconnect"),
              value: "queue_until_reconnect",
            },
          ]}
          value={deliveryMode}
        />
        <p className="command-actions-note">
          {isQueueMode
            ? t("commands.deliveryMode.queueUntilReconnectHint")
            : t("commands.deliveryMode.dispatchNowOnlyHint")}
        </p>
      </div>

      <div className="command-action-grid">
        {COMMAND_ACTIONS.map((kind) => {
          const requiredCapability = getCommandCapability(kind);
          const acceptedCapabilities = isQueueMode ? latestAcceptedCapabilities : activeAcceptedCapabilities;
          const queueModeReason =
            isQueueMode && !commandSupportsQueueing(kind)
              ? t("commands.disabledReason.queueUnavailable", {
                  kind: t(`commandKind.${kind}`, { defaultValue: kind }),
                })
              : null;
          const missingCapabilityReason =
            sessionBlockReason === null && !queueModeReason && !hasCommandCapability(kind, acceptedCapabilities)
              ? t("commands.disabledReason.missingCapability", { capability: requiredCapability })
              : null;
          const disabledReason = sessionBlockReason ?? queueModeReason ?? missingCapabilityReason;
          const isCurrentPending = isSubmitting && pendingKind === kind;

          return (
            <div className="command-action-item" key={kind}>
              <button
                className={
                  kind === "shutdown" || kind === "restart"
                    ? "button-secondary button-danger"
                    : "button-secondary"
                }
                disabled={disabledReason !== null || isSubmitting}
                onClick={() => void handleSubmit(kind)}
                title={disabledReason ?? undefined}
                type="button"
              >
                {isCurrentPending
                  ? t("commands.dispatchingAction", {
                      kind: t(`commandKind.${kind}`, { defaultValue: kind }),
                    })
                  : t(`commands.action.${kind}`)}
              </button>
              {queueModeReason || missingCapabilityReason ? (
                <p className="command-action-reason">{queueModeReason ?? missingCapabilityReason}</p>
              ) : null}
            </div>
          );
        })}
        {sessionBlockReason ? <p className="command-actions-note">{sessionBlockReason}</p> : null}
      </div>
    </div>
  );
}
