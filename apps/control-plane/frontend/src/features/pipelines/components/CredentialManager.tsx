import { useState, type FormEvent } from "react";

import type { PipelineCredential } from "../../../lib/api/types";

type CredentialInput = {
  name: string;
  connector_type: string;
  config: Record<string, unknown>;
  env_vars: Record<string, string>;
};

type CredentialManagerProps = {
  credentials: PipelineCredential[];
  onCreate: (input: CredentialInput) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onUpdate: (id: string, input: CredentialInput) => Promise<void>;
};

const CONNECTOR_TYPES = ["mysql", "postgres", "rabbitmq", "redis", "sqs", "feishu_bitable"];

export function CredentialManager({
  credentials,
  onCreate,
  onDelete,
  onUpdate,
}: CredentialManagerProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [connectorType, setConnectorType] = useState("mysql");
  const [dsn, setDsn] = useState("");
  const [secret, setSecret] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const config: Record<string, unknown> = dsn ? { dsn } : {};
    const env_vars: Record<string, string> = secret ? { PASSWORD: secret } : {};
    const payload: CredentialInput = {
      name,
      connector_type: connectorType,
      config,
      env_vars,
    };
    if (editingId) {
      await onUpdate(editingId, payload);
    } else {
      await onCreate(payload);
    }
    reset();
  }

  function edit(credential: PipelineCredential) {
    setEditingId(credential.id);
    setName(credential.name);
    setConnectorType(credential.connector_type);
    setDsn(String(credential.config.dsn ?? credential.config.url ?? ""));
    setSecret(credential.env_vars.PASSWORD ?? "");
  }

  function reset() {
    setEditingId(null);
    setName("");
    setConnectorType("mysql");
    setDsn("");
    setSecret("");
  }

  return (
    <section className="pipeline-credential-manager">
      <div className="pipeline-panel-heading">
        <span>Credentials</span>
        <h3>Connection Library</h3>
      </div>
      <div className="pipeline-credential-list">
        {credentials.length ? (
          credentials.map((credential) => (
            <article className="pipeline-credential-row" key={credential.id}>
              <strong>{credential.name}</strong>
              <span>{credential.connector_type}</span>
              <button onClick={() => edit(credential)} type="button">
                Edit
              </button>
              <button onClick={() => onDelete(credential.id)} type="button">
                Delete
              </button>
            </article>
          ))
        ) : (
          <p>No credentials saved.</p>
        )}
      </div>
      <form className="pipeline-credential-form" onSubmit={submit}>
        <label className="field">
          <span>Name</span>
          <input onChange={(event) => setName(event.target.value)} required value={name} />
        </label>
        <label className="field">
          <span>Type</span>
          <select
            onChange={(event) => setConnectorType(event.target.value)}
            value={connectorType}
          >
            {CONNECTOR_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>DSN / URL</span>
          <input onChange={(event) => setDsn(event.target.value)} value={dsn} />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            onChange={(event) => setSecret(event.target.value)}
            type="password"
            value={secret}
          />
        </label>
        <div className="ref-page-actions">
          <button type="submit">{editingId ? "Update credential" : "Add credential"}</button>
          {editingId ? (
            <button onClick={reset} type="button">
              Cancel
            </button>
          ) : null}
        </div>
      </form>
    </section>
  );
}
