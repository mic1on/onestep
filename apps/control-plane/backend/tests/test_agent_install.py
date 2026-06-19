def test_agent_install_script_uses_request_origin(client) -> None:
    response = client.get(
        "/agent-install.sh",
        headers={
            "host": "plane.example.com",
            "x-forwarded-proto": "https",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-shellscript")
    body = response.text
    assert "PLANE_URL=https://plane.example.com" in body
    assert "python3 -m pip install --upgrade onestep-worker-agent" in body
    assert "--registration-token \"$TOKEN\"" in body
    assert "--name \"$NAME\"" in body
    assert "--max-concurrency \"$MAX_CONCURRENCY\"" in body
    assert "\"${ONESTEP_AGENT[@]}\" start" in body


def test_agent_install_script_honors_forwarded_prefix(client) -> None:
    response = client.get(
        "/agent-install.sh",
        headers={
            "host": "internal.example.local",
            "x-forwarded-host": "plane.example.com",
            "x-forwarded-prefix": "/control-plane",
            "x-forwarded-proto": "https",
        },
    )

    assert response.status_code == 200
    assert "PLANE_URL=https://plane.example.com/control-plane" in response.text
