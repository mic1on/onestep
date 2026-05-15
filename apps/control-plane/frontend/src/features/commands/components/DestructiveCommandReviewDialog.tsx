import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { OverflowDialog } from "../../../components/ui/OverflowDialog";
import { useToast } from "../../../components/ui/ToastProvider";
import type { CommandRiskLevel } from "../../../lib/api/types";

type DestructiveCommandReviewDialogProps = {
  open: boolean;
  title: string;
  description: string;
  commandLabel: string;
  targetSummary: string;
  riskLevel: CommandRiskLevel;
  isSubmitting: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => Promise<void>;
};

export function DestructiveCommandReviewDialog({
  open,
  title,
  description,
  commandLabel,
  targetSummary,
  riskLevel,
  isSubmitting,
  onCancel,
  onConfirm,
}: DestructiveCommandReviewDialogProps) {
  const { t } = useTranslation();
  const { pushToast } = useToast();
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setReason("");
      setAcknowledged(false);
      setError(null);
    }
  }, [open]);

  if (!open) {
    return null;
  }

  async function handleConfirm() {
    const normalizedReason = reason.trim();
    if (!normalizedReason) {
      const message = t("destructiveCommandReview.reasonRequired");
      setError(message);
      pushToast({ tone: "error", message });
      return;
    }

    if (!acknowledged) {
      const message = t("destructiveCommandReview.acknowledgmentRequired");
      setError(message);
      pushToast({ tone: "error", message });
      return;
    }

    setError(null);
    try {
      await onConfirm(normalizedReason);
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : String(caughtError);
      setError(message);
      pushToast({ tone: "error", message });
    }
  }

  return (
    <OverflowDialog
      className="detail-dialog-card destructive-review-dialog"
      description={description}
      onClose={onCancel}
      open={open}
      title={title}
    >
      <div className="destructive-review-dialog-body">
        <div className="destructive-review-grid">
          <article className="destructive-review-card">
            <span>{t("destructiveCommandReview.commandLabel")}</span>
            <strong>{commandLabel}</strong>
          </article>
          <article className="destructive-review-card">
            <span>{t("destructiveCommandReview.targetLabel")}</span>
            <strong>{targetSummary}</strong>
          </article>
          <article className={`destructive-review-card is-${riskLevel}`}>
            <span>{t("destructiveCommandReview.riskLabel")}</span>
            <strong>{t(`destructiveCommandReview.risk.${riskLevel}`)}</strong>
          </article>
        </div>

        <label className="dialog-field">
          <span>{t("destructiveCommandReview.reasonLabel")}</span>
          <textarea
            onChange={(event) => setReason(event.target.value)}
            placeholder={t("destructiveCommandReview.reasonPlaceholder")}
            rows={4}
            value={reason}
          />
        </label>

        <label className="destructive-review-check">
          <input
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            type="checkbox"
          />
          <span>{t("destructiveCommandReview.acknowledgment")}</span>
        </label>

        {error ? <p className="inline-feedback inline-feedback-error">{error}</p> : null}

        <div className="dialog-actions">
          <button className="button-link" disabled={isSubmitting} onClick={onCancel} type="button">
            {t("destructiveCommandReview.cancel")}
          </button>
          <button className="button-secondary" disabled={isSubmitting} onClick={() => void handleConfirm()} type="button">
            {isSubmitting ? t("destructiveCommandReview.submitting") : t("destructiveCommandReview.confirm")}
          </button>
        </div>
      </div>
    </OverflowDialog>
  );
}
