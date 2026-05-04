import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useToast } from "../../../components/ui/ToastProvider";

type CommandReasonDialogProps = {
  open: boolean;
  title: string;
  description: string;
  isSubmitting: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => Promise<void>;
};

export function CommandReasonDialog({
  open,
  title,
  description,
  isSubmitting,
  onCancel,
  onConfirm,
}: CommandReasonDialogProps) {
  const { t } = useTranslation();
  const { pushToast } = useToast();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setReason("");
      setError(null);
    }
  }, [open]);

  if (!open) {
    return null;
  }

  async function handleConfirm() {
    const normalizedReason = reason.trim();
    if (!normalizedReason) {
      const message = t("commandReasonDialog.reasonRequired");
      setError(message);
      pushToast({ tone: "error", message });
      return;
    }
    setError(null);
    try {
      await onConfirm(normalizedReason);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setError(message);
      pushToast({ tone: "error", message });
    }
  }

  return (
    <div className="dialog-overlay" role="presentation">
      <div
        aria-describedby="command-reason-dialog-description"
        aria-labelledby="command-reason-dialog-title"
        aria-modal="true"
        className="dialog-card"
        role="dialog"
      >
        <div className="dialog-copy">
          <h3 id="command-reason-dialog-title">{title}</h3>
          <p id="command-reason-dialog-description">{description}</p>
        </div>

        <label className="dialog-field">
          <span>{t("commandReasonDialog.reasonLabel")}</span>
          <textarea
            onChange={(event) => setReason(event.target.value)}
            placeholder={t("commandReasonDialog.reasonPlaceholder")}
            rows={4}
            value={reason}
          />
        </label>

        <div className="dialog-actions">
          <button className="button-link" disabled={isSubmitting} onClick={onCancel} type="button">
            {t("commandReasonDialog.cancel")}
          </button>
          <button className="button-secondary" disabled={isSubmitting} onClick={() => void handleConfirm()} type="button">
            {isSubmitting ? t("commandReasonDialog.submitting") : t("commandReasonDialog.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
