"""Contract guard: the published JSON Schema must match strict validation.

The schema is *derived* from the same ``_STRICT_*`` field sets and the live
resource catalog that ``onestep check --strict`` enforces. Two failure modes
would make it worse than useless, and both are asserted here:

1. **Drift** — ``docs/public/schema/v1alpha1.json`` is a committed copy served
   at a stable URL. If it stops matching the generator, an editor validating a
   correct file starts reporting phantom errors.
2. **False negatives** — a schema that is *stricter* than the runtime rejects
   valid documents. This is not hypothetical: the resource catalog advertises
   ``host``/``username``/``password`` for ``mysql``/``postgres``/``rabbitmq``/
   ``redis`` and over-claims ``required`` (``dsn``, ``path``), yet strict
   validation rejects those fields and accepts documents omitting them. The
   schema must follow ``handler.allowed_fields``, not the catalog.

``jsonschema`` is not a runtime or test dependency, so the validator-backed
assertions skip when it is unavailable; the drift and field-set assertions run
everywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from onestep.schema import SCHEMA_ID, _official_registry, build_app_schema, schema_json

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISHED = REPO_ROOT / "docs" / "public" / "schema" / "v1alpha1.json"


def _registry():
    """The registry the generator uses — installed plugins only.

    Deliberately not the global default registry: tests such as
    ``test_resource_registry.py`` register ad-hoc types there, and since CI runs
    the whole suite in one process those would otherwise leak into the schema
    and make these assertions environment-order dependent.
    """
    return _official_registry()


def test_published_schema_matches_generator() -> None:
    """Every resource type this environment can see must match the served copy.

    The published schema is generated from the complete official connector set,
    but the set that can be *installed* varies by environment: CI runs Python
    3.9-3.12 and ``onestep-kafka`` requires >=3.10, so the 3.9 job registers
    fewer types. A whole-document byte comparison therefore cannot hold
    everywhere, and asserting it would fail CI on a legitimate environment gap.

    Instead this compares per resource type: anything registered here must be
    byte-identical to its published definition, and the published copy must not
    be missing a type this environment produced. When the environment happens to
    be complete the whole document must also match exactly, which is the
    strongest form of the same guarantee.
    """
    assert PUBLISHED.is_file(), f"missing published schema: {PUBLISHED}"
    published_document = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    generated_document = build_app_schema()

    published_defs = published_document["$defs"]
    generated_defs = generated_document["$defs"]

    published_types = {name for name in published_defs if name.startswith("resource_")}
    generated_types = {name for name in generated_defs if name.startswith("resource_")}

    unexpected = generated_types - published_types
    assert not unexpected, (
        "the generator produced resource types missing from the published schema: "
        f"{sorted(unexpected)}; regenerate with "
        "`onestep schema --out docs/public/schema/v1alpha1.json`"
    )

    drifted = sorted(
        name
        for name in generated_types
        if published_defs[name] != generated_defs[name]
    )
    assert not drifted, (
        "published schema drifted from the generator for: "
        f"{[n.removeprefix('resource_') for n in drifted]}; regenerate with "
        "`onestep schema --out docs/public/schema/v1alpha1.json`"
    )

    # Non-resource definitions (task, emitBinding, handler) and the top-level
    # shape are environment-independent, so they are always compared exactly.
    for name in ("task", "emitBinding", "handler"):
        assert published_defs.get(name) == generated_defs.get(name), name
    assert published_document["properties"] == generated_document["properties"]
    assert published_document.get("required") == generated_document.get("required")

    if generated_types == published_types:
        # Complete environment: the whole document must match byte-for-byte.
        assert PUBLISHED.read_text(encoding="utf-8") == schema_json(), (
            "docs/public/schema/v1alpha1.json is stale; regenerate with "
            "`onestep schema --out docs/public/schema/v1alpha1.json`"
        )
    # Otherwise some official connector is simply not installable here (e.g.
    # onestep-kafka needs Python >=3.10, so the 3.9 CI job registers fewer
    # types). That is an environment gap, not drift: the per-type comparison
    # above still pins every type this environment can produce.


def test_schema_identity_is_stable() -> None:
    document = build_app_schema()
    assert document["$id"] == SCHEMA_ID
    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_resource_properties_follow_allowed_fields() -> None:
    """Each resource def must expose exactly the fields strict mode allows.

    This is the assertion that would have caught using the catalog's ``fields``:
    ``mysql`` would have advertised ``host``/``username``, which strict
    validation rejects.
    """
    document = build_app_schema()
    registry = _registry()
    defs = document["$defs"]

    checked = 0
    for entry in registry.catalog_entries(None):
        handler = registry.get_resource_handler(entry.type)
        if handler is None or handler.allowed_fields is None:
            continue
        definition = defs[f"resource_{entry.type}"]
        assert definition["additionalProperties"] is False, entry.type
        assert set(definition["properties"]) == set(handler.allowed_fields), (
            f"{entry.type}: schema properties drifted from handler.allowed_fields"
        )
        checked += 1
    assert checked > 0, "no resource types were checked"


def test_schema_does_not_invent_required_fields() -> None:
    """A resource def must not require a field strict validation tolerates.

    The catalog over-claims required-ness (``mysql`` -> ``dsn``, ``webhook`` ->
    ``path``); strict validation accepts those documents. Emitting ``required``
    would make the schema reject valid configs.
    """
    document = build_app_schema()
    for name, definition in document["$defs"].items():
        if not name.startswith("resource_"):
            continue
        assert "required" not in definition, (
            f"{name} declares required fields; the catalog over-claims them"
        )


def test_every_catalog_type_has_a_schema_definition() -> None:
    document = build_app_schema()
    registry = _registry()
    for entry in registry.catalog_entries(None):
        assert f"resource_{entry.type}" in document["$defs"], entry.type
    assert len(document["$defs"]["resource"]["oneOf"]) == len(registry.catalog_entries(None))


def test_schema_accepts_dollar_schema_directive() -> None:
    """``$schema`` is documentation-only but must be a permitted top-level key."""
    from onestep.config import validate_app_config

    document = build_app_schema()
    assert "$schema" in document["properties"]
    validate_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "$schema": SCHEMA_ID,
            "app": {"name": "demo"},
            "resources": {"tick": {"type": "interval", "minutes": 5}},
            "tasks": [{"name": "x", "source": "tick", "handler": {"ref": "a.b:c"}}],
        }
    )


def test_strict_rejects_non_string_dollar_schema() -> None:
    from onestep.config import validate_app_config

    with pytest.raises(TypeError, match=r"\$schema"):
        validate_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "$schema": 123,
                "app": {"name": "demo"},
            }
        )


def test_schema_serialization_is_deterministic() -> None:
    assert schema_json() == schema_json()
    json.loads(schema_json())


# --- validator-backed assertions (skipped without jsonschema) -----------------


def _validator():
    jsonschema = pytest.importorskip("jsonschema")
    document = build_app_schema()
    jsonschema.Draft202012Validator.check_schema(document)
    return jsonschema.Draft202012Validator(document)


def _base(**overrides):
    document = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "demo"},
        "resources": {"tick": {"type": "interval", "minutes": 5}},
        "tasks": [{"name": "x", "source": "tick", "emit": "tick"}],
    }
    document.update(overrides)
    return document


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(_base(), id="minimal"),
        pytest.param(_base(**{"$schema": SCHEMA_ID}), id="with-dollar-schema"),
        pytest.param(
            _base(
                app={"name": "demo", "logging": {"level": "INFO", "format": "json"}},
                resources={"q": {"type": "memory", "maxsize": 10}},
                tasks=[{"name": "x", "source": "q", "emit": "q"}],
            ),
            id="logging-and-memory",
        ),
    ],
)
def test_schema_accepts_valid_documents(document) -> None:
    assert list(_validator().iter_errors(document)) == []


@pytest.mark.parametrize(
    "document, reason",
    [
        pytest.param(_base(bogus=1), "top-level unknown field", id="unknown-top-level"),
        pytest.param(
            _base(tasks=[{"name": "x", "source": "tick", "concurrencyy": 3}]),
            "task field typo",
            id="task-field-typo",
        ),
        pytest.param(
            _base(resources={"t": {"type": "no_such_type"}}),
            "unknown resource type",
            id="unknown-resource-type",
        ),
        pytest.param(
            _base(resources={"db": {"type": "mysql", "host": "h"}}),
            "catalog-only mysql field that strict mode rejects",
            id="mysql-host-rejected",
        ),
        pytest.param(
            _base(app={"name": "demo", "logging": {"level": "INFO", "format": "xml"}}),
            "invalid logging format",
            id="bad-logging-format",
        ),
        pytest.param(
            _base(tasks=[{"name": "x", "source": "tick"}]),
            "task without handler or emit",
            id="task-missing-handler-and-emit",
        ),
    ],
)
def test_schema_rejects_invalid_documents(document, reason) -> None:
    errors = list(_validator().iter_errors(document))
    assert errors, f"schema accepted an invalid document: {reason}"
