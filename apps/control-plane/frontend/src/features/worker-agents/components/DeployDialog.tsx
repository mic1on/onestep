import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import {
  useCreateWorkerDeploymentMutation,
  useCreateWorkflowPackageMutation,
  useWorkerAgentsQuery,
} from "../queries";
import { VibeButton } from "../../../components/ui/VibeButton";
import { VibeField } from "../../../components/ui/VibeField";
import { VibeInlineNotice } from "../../../components/ui/VibeInlineNotice";
import { VibeModal } from "../../../components/ui/VibeModal";
import { VibehubSelect } from "../../../components/ui/VibehubSelect";
import { VibehubUpload } from "../../../components/ui/VibehubUpload";
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
    <VibeModal
      footer={
        <>
          <VibeButton disabled={isPending} onClick={onClose} variant="secondary">
            {t("common.cancel")}
          </VibeButton>
          <VibeButton
            disabled={isPending}
            onClick={() => void handleDeploy()}
            variant="primary"
          >
            {isPending ? t("agentsDeploy.deploying") : t("agentsDeploy.deploy")}
          </VibeButton>
        </>
      }
      onClose={onClose}
      open={open}
      title={t("agentsDeploy.title")}
    >
      <VibehubUpload
        accept=".zip,application/zip"
        actionLabel={t("agentsDeploy.packageUploadAction")}
        badgeLabel={t("agentsDeploy.packageUploadBadge")}
        description={t("agentsDeploy.packageUploadDescription")}
        disabled={isPending}
        emptyText={t("agentsDeploy.packageUploadPrompt")}
        label={t("agentsDeploy.packageFile")}
        onChange={(nextFile) => {
          setFile(nextFile);
          setError(null);
        }}
        selectedText={t("agentsDeploy.packageUploadSelected")}
        value={file}
      />

      <VibehubSelect
        label={t("agentsDeploy.targetAgent")}
        onChange={setAgentId}
        options={[
          { value: "", label: t("agentsDeploy.selectAgent") },
          ...agents.map((agent) => ({
            value: agent.worker_agent_id,
            label: `${agent.display_name} (${agent.status})`,
          })),
        ]}
        value={agentId}
      />

      <VibeField
        label={t("agentsDeploy.version")}
        onChange={(event) => setVersion(event.target.value)}
        placeholder={t("agentsDeploy.versionPlaceholder")}
        type="text"
        value={version}
      />

      <VibehubSelect
        label={t("agentsDeploy.desiredStatus")}
        onChange={(nextValue) =>
          setDesiredStatus(nextValue as WorkerDeploymentDesiredStatus)
        }
        options={[
          { value: "running", label: t("agentsDeploy.statusRunning") },
          { value: "stopped", label: t("agentsDeploy.statusStopped") },
        ]}
        value={desiredStatus}
      />

      {error ? (
        <VibeInlineNotice variant="error">
          {error}
        </VibeInlineNotice>
      ) : null}
    </VibeModal>
  );
}
