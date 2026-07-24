from __future__ import annotations

import os
from uuid import UUID

from onestep_worker_agent.identity import AgentIdentity, load_identity, save_identity


def test_save_and_load_identity(tmp_path) -> None:
    path = tmp_path / "identity.json"
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="secret-token",
    )

    save_identity(path, identity)

    loaded = load_identity(path)
    assert loaded == identity


def test_save_identity_restricts_file_permissions(tmp_path) -> None:
    # connection_token is a long-lived credential: the file must be owner-only.
    path = tmp_path / "identity.json"
    identity = AgentIdentity(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_token="secret-token",
    )

    save_identity(path, identity)

    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600
