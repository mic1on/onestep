from __future__ import annotations

import io
import zipfile
from typing import Any

import yaml

#: Source/sink types that do NOT need a connector resource (built-in).
BUILTIN_SOURCE_TYPES = frozenset({"interval", "cron", "webhook", "memory"})
BUILTIN_SINK_TYPES = frozenset({"memory"})

#: Sink types that have no connector dependency.
NO_CONNECTOR_SINK_TYPES = frozenset({"http_sink"})
REPORTING_TOKEN_ENV = "ONESTEP_WORKER_REPORTING_TOKEN"


def _needs_connector(source_or_sink: dict[str, Any]) -> bool:
    typ = source_or_sink["type"]
    if source_or_sink.get("connector_id"):
        return True
    return typ not in BUILTIN_SOURCE_TYPES and typ not in NO_CONNECTOR_SINK_TYPES


def compile_worker_yaml(
    worker: dict[str, Any],
    connectors: dict[str, dict[str, Any]],
) -> str:
    """Compile a worker config + resolved connectors into a worker.yaml string.

    ``worker`` shape: {name, handler_ref, source: {type, connector_id, fields},
    sinks: [{type, connector_id, fields}, ...]}
    ``connectors`` shape: {connector_id: {type, config: {...}, secret: {...}}}
    """
    source = worker["source"]
    sinks = worker.get("sinks", [])

    resources: dict[str, dict[str, Any]] = {}
    # Map connector_id → resource key (dedup: same connector_id = same resource).
    conn_key_map: dict[str, str] = {}

    def resolve_connector(connector_id: str) -> str:
        if connector_id in conn_key_map:
            return conn_key_map[connector_id]
        conn = connectors[connector_id]
        key = f"conn_{len(conn_key_map)}"
        resource: dict[str, Any] = {"type": conn["type"]}
        resource.update(conn.get("config", {}))
        resource.update(conn.get("secret", {}))
        resources[key] = resource
        conn_key_map[connector_id] = key
        return key

    # Source resource.
    src_resource: dict[str, Any] = {"type": source["type"]}
    if _needs_connector(source) and source.get("connector_id"):
        src_resource["connector"] = resolve_connector(source["connector_id"])
    src_resource.update(source.get("fields", {}))
    resources["source_0"] = src_resource

    # Sink resources.
    for index, sink in enumerate(sinks):
        sink_resource: dict[str, Any] = {"type": sink["type"]}
        if _needs_connector(sink) and sink.get("connector_id"):
            sink_resource["connector"] = resolve_connector(sink["connector_id"])
        sink_resource.update(sink.get("fields", {}))
        resources[f"sink_{index}"] = sink_resource

    # Single task.
    task: dict[str, Any] = {
        "name": "main",
        "source": "source_0",
        "handler": {"ref": worker["handler_ref"]},
    }
    if sinks:
        task["emit"] = [f"sink_{i}" for i in range(len(sinks))]

    doc: dict[str, Any] = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": worker["name"]},
        "resources": resources,
        "tasks": [task],
    }
    reporter = _reporter_config(worker)
    if reporter is not None:
        doc["reporter"] = reporter
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def _reporter_config(worker: dict[str, Any]) -> bool | dict[str, str] | None:
    if worker.get("reporting_enabled", True) is False:
        return None
    reporting_config = worker.get("reporting_config")
    if not isinstance(reporting_config, dict):
        return True
    if reporting_config.get("mode", "platform") != "custom":
        return True
    endpoint_url = str(reporting_config.get("endpoint_url") or "").strip()
    if not endpoint_url:
        raise ValueError("custom reporting endpoint_url is required")
    return {
        "base_url": endpoint_url,
        "token": f"${{{REPORTING_TOKEN_ENV}}}",
    }


def merge_package(handler_zip_bytes: bytes, worker_yaml_str: str) -> bytes:
    """Merge a compiled worker.yaml into a handler zip, overwriting any existing one."""
    out_buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(handler_zip_bytes), "r") as src_zip:
        with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for item in src_zip.infolist():
                if item.filename == "worker.yaml":
                    continue  # overwrite with the compiled one
                out_zip.writestr(item, src_zip.read(item.filename))
            out_zip.writestr("worker.yaml", worker_yaml_str)
    return out_buf.getvalue()
