import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { VibeActionGroup } from "../../components/ui/VibeActionGroup";
import { VibeButton } from "../../components/ui/VibeButton";
import { VibeField } from "../../components/ui/VibeField";
import { VibeModal } from "../../components/ui/VibeModal";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { sourceTypeSchemas } from "../../features/workers/catalog";
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
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null);

  function sourceTypeLabel(type: string) {
    return t(`workerEditor.sourceTypeLabels.${type}`, {
      defaultValue: sourceTypeSchemas[type]?.label ?? type,
    });
  }

  const selectedWorker = useMemo(() => {
    if (workers.length === 0) return null;
    return workers.find((worker) => worker.id === selectedWorkerId) ?? workers[0];
  }, [selectedWorkerId, workers]);

  useEffect(() => {
    if (workers.length === 0) {
      setSelectedWorkerId(null);
      return;
    }
    if (!selectedWorkerId || !workers.some((worker) => worker.id === selectedWorkerId)) {
      setSelectedWorkerId(workers[0].id);
    }
  }, [selectedWorkerId, workers]);

  function statusLabel(status: string) {
    return status === "ready" ? t("workersList.statusReady") : t("workersList.statusDraft");
  }

  function targetCountLabel(count: number) {
    return t(count === 1 ? "workersList.targetCountOne" : "workersList.targetCountMany", { count });
  }

  function packageLabel(packageId: string | null) {
    return packageId ? t("workersList.packageReady") : t("workersList.packageMissing");
  }

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
          <div className="signal-console-hero-actions signal-console-header-actions workers-list-hero-actions">
            <VibeActionGroup>
              <VibeButton icon="+" onClick={() => setCreateOpen(true)} variant="primary">
                {t("workersList.newWorker")}
              </VibeButton>
            </VibeActionGroup>
          </div>
        }
      />

      <VibeSummaryStrip
        className="runtime-summary-strip"
        items={[
          { label: t("workersList.summaryWorkers"), value: workers.length },
          { label: t("workersList.summaryReady"), tone: "success", value: readyCount },
          {
            label: t("workersList.summaryDraft"),
            tone: draftCount > 0 ? "danger" : "default",
            value: draftCount,
          },
          { label: t("workersList.summarySinks"), value: sinkCount },
        ]}
      />

      {workersQuery.error ? (
        <EmptyState title={t("workersList.loadErrorTitle")} body={String(workersQuery.error)} />
      ) : null}
      {workersQuery.isPending ? (
        <div className="loading-block">{t("workersList.loading")}</div>
      ) : null}
      {!workersQuery.isPending && !workersQuery.error && workers.length === 0 ? (
        <EmptyState
          title={t("workersList.emptyTitle")}
          body={t("workersList.emptyBody")}
          action={
            <VibeButton icon="+" onClick={() => setCreateOpen(true)} variant="primary">
              {t("workersList.newWorker")}
            </VibeButton>
          }
        />
      ) : null}

      {workers.length > 0 && selectedWorker ? (
        <section className="workers-asset-workbench" aria-label={t("workersList.workbenchLabel")}>
          <aside className="workers-asset-rail" aria-label={t("workersList.libraryTitle")}>
            <div className="workers-asset-rail-head">
              <span>{t("workersList.libraryEyebrow")}</span>
              <h3>{t("workersList.libraryTitle")}</h3>
            </div>
            <div className="workers-asset-list">
              {workers.map((worker) => {
                const selected = worker.id === selectedWorker.id;
                return (
                  <button
                    className={selected ? "workers-asset-card is-selected" : "workers-asset-card"}
                    key={worker.id}
                    onClick={() => setSelectedWorkerId(worker.id)}
                    type="button"
                  >
                    <span className="workers-asset-glyph" aria-hidden="true">ST</span>
                    <span className="workers-asset-copy">
                      <strong>{worker.name}</strong>
                      <span>
                        {statusLabel(worker.status)} · {targetCountLabel(worker.sink_configs.length)}
                      </span>
                    </span>
                    <span className={worker.status === "ready" ? "workers-asset-status is-ready" : "workers-asset-status"}>
                      {statusLabel(worker.status)}
                    </span>
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="workers-detail-surface">
            <header className="workers-detail-head">
              <div>
                <span>{t("workersList.selectedStepLabel")}</span>
                <h3>{selectedWorker.name}</h3>
                <p>{selectedWorker.description || t("workersList.noDescription")}</p>
              </div>
              <Link className="vibe-button vibe-button-primary workers-open-studio" to={`/workers/${encodeURIComponent(selectedWorker.id)}`}>
                <span className="vibe-button-label">{t("workersList.openStudio")}</span>
              </Link>
            </header>

            <div className="workers-detail-grid">
              <section className="workers-route-panel">
                <div className="workers-panel-heading">
                  <span>{t("workersList.routeSummaryTitle")}</span>
                  <strong>{sourceTypeLabel(selectedWorker.source_config.type)}</strong>
                </div>
                <div className="workers-route-facts">
                  <article>
                    <span>{t("workersList.statusTitle")}</span>
                    <strong>{statusLabel(selectedWorker.status)}</strong>
                  </article>
                  <article>
                    <span>{t("workersList.handlerTitle")}</span>
                    <strong>{selectedWorker.handler_ref}</strong>
                  </article>
                  <article>
                    <span>{t("workersList.targetsTitle")}</span>
                    <strong>{targetCountLabel(selectedWorker.sink_configs.length)}</strong>
                  </article>
                </div>
              </section>

              <aside className="workers-runbook-panel">
                <span>{t("workersList.runbookEyebrow")}</span>
                <h4>{t("workersList.runbookTitle")}</h4>
                <div className="workers-runbook-facts">
                  <div>
                    <span>{t("workersList.packageTitle")}</span>
                    <strong>{packageLabel(selectedWorker.handler_package_id)}</strong>
                  </div>
                  <div>
                    <span>{t("workersList.reportingTitle")}</span>
                    <strong>{selectedWorker.reporting_enabled ? t("workersList.reportingOn") : t("workersList.reportingOff")}</strong>
                  </div>
                  <div>
                    <span>{t("workersList.updatedTitle")}</span>
                    <strong>{formatRelativeTime(selectedWorker.updated_at)}</strong>
                  </div>
                </div>
              </aside>
            </div>
          </section>
        </section>
      ) : null}

      <VibeModal
        footer={
          <>
            <VibeButton onClick={() => setCreateOpen(false)} variant="secondary">
              {t("common.cancel")}
            </VibeButton>
            <VibeButton
              disabled={!name.trim() || createMutation.isPending}
              onClick={() => void handleCreate()}
              variant="primary"
            >
              {t("workerEditor.save")}
            </VibeButton>
          </>
        }
        onClose={() => setCreateOpen(false)}
        open={createOpen}
        title={t("workersList.newWorker")}
      >
        <VibeField
          autoFocus
          label={t("workerEditor.name")}
          onChange={(event) => setName(event.target.value)}
          type="text"
          value={name}
        />
        <VibeField
          label={t("workerEditor.description")}
          multiline
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          value={description}
        />
      </VibeModal>
    </div>
  );
}
