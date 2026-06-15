import type {
  PipelineConnectorDescriptor,
  PipelineCredential,
  PipelineGraphNode,
} from "../../../lib/api/types";

type PropertyPanelProps = {
  connector: PipelineConnectorDescriptor | null;
  credentials: PipelineCredential[];
  node: PipelineGraphNode | null;
  onChange: (node: PipelineGraphNode) => void;
};

export function PropertyPanel({ connector, credentials, node, onChange }: PropertyPanelProps) {
  if (!node || !connector) {
    return (
      <aside className="pipeline-property-panel">
        <h3>Properties</h3>
        <p>Select a node to configure connector fields and credentials.</p>
      </aside>
    );
  }
  const activeNode = node;

  function patch(partial: Partial<PipelineGraphNode>) {
    onChange({ ...activeNode, ...partial });
  }

  function setConfig(name: string, value: string, type: string) {
    const config = { ...activeNode.config };
    if (!value.trim()) {
      delete config[name];
    } else {
      config[name] = type === "number" ? Number(value) : value;
    }
    patch({ config });
  }

  function setMapping(key: string, value: string) {
    patch({ mapping: { ...activeNode.mapping, [key]: value } });
  }

  const matchingCredentials = connector.credential_type
    ? credentials.filter((credential) => credential.connector_type === connector.credential_type)
    : credentials;

  return (
    <aside className="pipeline-property-panel">
      <div className="pipeline-panel-heading">
        <span>{connector.category}</span>
        <h3>{connector.label}</h3>
        <code>{activeNode.id}</code>
      </div>

      {connector.credential_type ? (
        <label className="field">
          <span>Credential</span>
          <select
            onChange={(event) => patch({ credential_ref: event.target.value || null })}
            value={activeNode.credential_ref ?? ""}
          >
            <option value="">Direct input</option>
            {matchingCredentials.map((credential) => (
              <option key={credential.id} value={credential.id}>
                {credential.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {connector.fields.map((field) => (
        <label className="field" key={field.name}>
          <span>{field.label}</span>
          <input
            onChange={(event) => setConfig(field.name, event.target.value, field.type)}
            type={field.type === "number" ? "number" : "text"}
            value={String(activeNode.config[field.name] ?? "")}
          />
        </label>
      ))}

      {connector.category === "handler" ? (
        <section className="pipeline-property-section">
          <div className="segmented">
            <button
              className={activeNode.mode !== "code" ? "active" : ""}
              onClick={() => patch({ mode: "visual" })}
              type="button"
            >
              Mapping
            </button>
            <button
              className={activeNode.mode === "code" ? "active" : ""}
              onClick={() => patch({ mode: "code" })}
              type="button"
            >
              Python
            </button>
          </div>
          {activeNode.mode === "code" ? (
            <label className="field">
              <span>Code</span>
              <textarea
                onChange={(event) => patch({ code: event.target.value })}
                rows={10}
                value={activeNode.code ?? "async def handler(ctx, payload):\n    return payload\n"}
              />
            </label>
          ) : (
            <label className="field">
              <span>Mapping</span>
              <input
                onChange={(event) => setMapping("value", event.target.value)}
                value={activeNode.mapping.value ?? "{{ payload }}"}
              />
            </label>
          )}
        </section>
      ) : null}
    </aside>
  );
}
