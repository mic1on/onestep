from __future__ import annotations

from onestep.resource_registry import ResourceRegistry
from onestep_feishu_bitable import register


def test_feishu_bitable_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["feishu_bitable"].fields}
    source_fields = {field.name: field for field in catalog["feishu_bitable_incremental"].fields}
    sink_fields = {field.name: field for field in catalog["feishu_bitable_table_sink"].fields}

    assert catalog["feishu_bitable"].roles == ("connector",)
    assert connector_fields["app_id"].required is True
    assert connector_fields["app_secret"].required is True
    assert connector_fields["app_secret"].secret is True
    assert catalog["feishu_bitable_incremental"].roles == ("source",)
    assert catalog["feishu_bitable_incremental"].connector_types == ("feishu_bitable",)
    assert source_fields["app_token"].secret is True
    assert catalog["feishu_bitable_table_sink"].roles == ("sink",)
    assert catalog["feishu_bitable_table_sink"].connector_types == ("feishu_bitable",)
    assert sink_fields["app_token"].secret is True
