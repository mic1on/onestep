import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import {
  useCreateWorkerDeploymentMutation,
  useCreateWorkflowPackageMutation,
  useWorkerAgentsQuery,
} from "../queries";
import type { WorkerDeploymentDesiredStatus } from "../../../lib/api/types";

type DeployDialogProps = {
  open: boolean;
  onClose: () => void;
};

export function DeployDialog({ open, onClose }: DeployDialogProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const agentsQuery = useWorkerAgentsQuery();
  const packageMutation = useCreateWorkflowPackageMutation();
  const deploymentMutation = useCreateWorkerDeploymentMutation();
  const [agentId, setAgentId] = useState("");
  const [desiredStatus, setDesiredStatus] = useState<WorkerDeploymentDesiredStatus>("running");
  const [version, setVersion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return null;
  }

  const agents = agentsQuery.data?.items ?? [];
  const isPending = packageMutation.isPending || deploymentMutation.isPending;

  async function handleDeploy() {
    setError(null);
    if (!file) {
      setError(t("agentsDeploy.errorNoFile"));
      return;
    }
    if (!agentId) {
      setError(t("agentsDeploy.errorNoAgent"));
      return;
    }
    const effectiveVersion = version || new Date().toISOString();
    try {
      const pkg = await packageMutation.mutateAsync({
        file,
        workflowId: "manual-upload",
        version: effectiveVersion,
        filename: file.name,
      });
      const deployment = await deploymentMutation.mutateAsync({
        workflowPackageId: pkg.package_id,
        workerAgentId: agentId,
        desiredStatus,
      });
      onClose();
      navigate(`/agents/${encodeURIComponent(deployment.worker_agent_id)}`);
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <div className="ref-modal-overlay" role="dialog" aria-modal="true">
      <div className="ref-modal-card">
        <header className="ref-modal-head">
          <strong>{t("agentsDeploy.title")}</strong>
          <button type="button" onClick={onClose} aria-label={t("common.close")}>
            ×
          </button>
        </header>
        <div className="ref-modal-body">
          <label className="ref-inline-control ref-inline-control-search">
            <span>{t("agentsDeploy.packageFile")}</span>
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>

          <label className="ref-inline-control ref-inline-control-select">
            <span>{t("agentsDeploy.targetAgent")}</span>
            <select onChange={(event) => setAgentId(event.target.value)} value={agentId}>
              <option value="">{t("agentsDeploy.selectAgent")}</option>
              {agents.map((agent) => (
                <option key={agent.worker_agent_id} value={agent.worker_agent_id}>
                  {agent.display_name} ({agent.status})
                </option>
              ))}
            </select>
          </label>

          <label className="ref-inline-control">
            <span>{t("agentsDeploy.version")}</span>
            <input
              type="text"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              placeholder={t("agentsDeploy.versionPlaceholder")}
            />
          </label>

          <label className="ref-inline-control ref-inline-control-select">
            <span>{t("agentsDeploy.desiredStatus")}</span>
            <select
              onChange={(event) =>
                setDesiredStatus(event.target.value as WorkerDeploymentDesiredStatus)
              }
              value={desiredStatus}
            >
              <option value="running">{t("agentsDeploy.statusRunning")}</option>
              <option value="stopped">{t("agentsDeploy.statusStopped")}</option>
            </select>
          </label>

          {error ? <p className="ref-error-note">{error}</p> : null}
        </div>
        <footer className="ref-modal-foot">
          <button type="button" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </button>
          <button type="button" onClick={() => void handleDeploy()} disabled={isPending}>
            {isPending ? t("agentsDeploy.deploying") : t("agentsDeploy.deploy")}
          </button>
        </footer>
      </div>
    </div>
  );
}
