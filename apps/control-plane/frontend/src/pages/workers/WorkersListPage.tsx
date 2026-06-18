import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCreateWorkerMutation, useWorkersQuery } from "../../features/workers/queries";
import { formatRelativeTime } from "../../lib/formatters";

export function WorkersListPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const workersQuery = useWorkersQuery();
  const createMutation = useCreateWorkerMutation();
  const workers = workersQuery.data?.items ?? [];
  const readyCount = workers.filter((worker) => worker.status === "ready").length;
  const draftCount = workers.length - readyCount;
  const sinkCount = workers.reduce((total, worker) => total + worker.sink_configs.length, 0);

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  async function handleCreate() {
    if (!name.trim()) return;
    const worker = await createMutation.mutateAsync({
      name: name.trim(),
      description: description.trim(),
    });
    setCreateOpen(false);
    setName("");
    setDescription("");
    navigate(`/workers/${encodeURIComponent(worker.id)}`);
  }

  return (
    <div className="ref-console-page signal-console-runtime-page workers-list-page">
      <SignalConsoleHeader
        kicker={t("workersList.eyebrow")}
        title={t("workersList.title")}
        description={<p className="signal-console-hero-note">{t("workersList.subtitle")}</p>}
        side={
          <button type="button" onClick={() => setCreateOpen(true)}>
            {t("workersList.newWorker")}
          </button>
        }
      />

      <section className="ref-summary-strip runtime-summary-strip">
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("workersList.summaryWorkers")}</span>
          <strong>{workers.length}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-success">
          <span>{t("workersList.summaryReady")}</span>
          <strong>{readyCount}</strong>
        </article>
        <article
          className={
            draftCount > 0
              ? "ref-summary-chip ref-summary-chip-danger"
              : "ref-summary-chip ref-summary-chip-default"
          }
        >
          <span>{t("workersList.summaryDraft")}</span>
          <strong>{draftCount}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("workersList.summarySinks")}</span>
          <strong>{sinkCount}</strong>
        </article>
      </section>

      {workersQuery.error ? (
        <EmptyState title={t("workersList.loadErrorTitle")} body={String(workersQuery.error)} />
      ) : null}
      {workersQuery.isPending ? (
        <div className="loading-block">{t("workersList.loading")}</div>
      ) : null}
      {!workersQuery.isPending && !workersQuery.error && workers.length === 0 ? (
        <EmptyState title={t("workersList.emptyTitle")} body={t("workersList.emptyBody")} />
      ) : null}

      {workers.length > 0 ? (
        <section className="ref-table-card runtime-table-card workers-table-card">
          <div className="ref-table-head runtime-table-head runtime-workers-grid">
            <span>{t("workersList.tableHeaderName")}</span>
            <span>{t("workersList.tableHeaderStatus")}</span>
            <span>{t("workersList.tableHeaderSource")}</span>
            <span>{t("workersList.tableHeaderSinks")}</span>
            <span>{t("workersList.tableHeaderUpdated")}</span>
          </div>
          <div className="ref-table-body">
            {workers.map((worker) => (
              <article className="ref-table-row runtime-table-row runtime-workers-grid" key={worker.id}>
                <div className="ref-service-cell">
                  <Link className="ref-service-link" to={`/workers/${encodeURIComponent(worker.id)}`}>
                    <strong>{worker.name}</strong>
                    <span>{worker.description}</span>
                  </Link>
                </div>
                <div className="ref-meta-cell">
                  <StatusBadge value={worker.status === "ready" ? "active" : "pending"} />
                </div>
                <div className="ref-meta-cell">
                  <strong>{worker.source_config.type}</strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{worker.sink_configs.length}</strong>
                </div>
                <div className="ref-meta-cell">
                  <strong>{formatRelativeTime(worker.updated_at)}</strong>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {createOpen ? (
        <div className="ref-modal-overlay" role="dialog" aria-modal="true">
          <div className="ref-modal-card">
            <header className="ref-modal-head">
              <strong>{t("workersList.newWorker")}</strong>
              <button type="button" onClick={() => setCreateOpen(false)} aria-label={t("common.close")}>
                ×
              </button>
            </header>
            <div className="ref-modal-body">
              <label className="ref-inline-control">
                <span>{t("workerEditor.name")}</span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                />
              </label>
              <label className="ref-inline-control">
                <span>{t("workerEditor.description")}</span>
                <textarea
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
            </div>
            <footer className="ref-modal-foot">
              <button type="button" onClick={() => setCreateOpen(false)}>
                {t("common.cancel")}
              </button>
              <button
                type="button"
                disabled={!name.trim() || createMutation.isPending}
                onClick={() => void handleCreate()}
              >
                {t("workerEditor.save")}
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </div>
  );
}
