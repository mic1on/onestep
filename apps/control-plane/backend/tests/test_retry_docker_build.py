"""Guards for the image-build retry wrapper and its CI wiring.

Base-image pulls fail for reasons unrelated to this repository: ECR Public
answers anonymous pulls with toomanyrequests / Data limit exceeded, and GitHub's
hosted runners share IP ranges with everyone else. The wrapper in
scripts/retry-docker-build.sh retries only those transient failures, and the
workflow must route every build through it, or a rate limit fails a release
instead of a build.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_DIR.parents[1]
SCRIPT = APP_DIR / "scripts" / "retry-docker-build.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "control-plane.yml"
SMOKE_SCRIPT = APP_DIR / "scripts" / "run-smoke.sh"


def run_script(*args: str, attempts: str = "3") -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RETRY_DOCKER_MAX_ATTEMPTS": attempts,
        "RETRY_DOCKER_INITIAL_DELAY": "0",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_script_exists_and_is_valid_bash() -> None:
    assert SCRIPT.exists()
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_transient_rate_limit_is_retried(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    command = (
        f"n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; "
        'if [ "$n" -lt 2 ]; then '
        'echo "429 Too Many Requests: toomanyrequests: Data limit exceeded" >&2; exit 1; '
        "fi; echo built"
    )

    result = run_script("sh", "-c", command)

    assert result.returncode == 0, result.stderr
    assert counter.read_text().strip() == "2"
    assert "transient failure detected" in result.stderr


def test_non_transient_failure_is_not_retried(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    command = (
        f"n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; "
        'echo "failed to solve: Dockerfile parse error" >&2; exit 1'
    )

    result = run_script("sh", "-c", command)

    assert result.returncode == 1
    assert counter.read_text().strip() == "1"
    assert "does not look transient" in result.stderr


def test_exhausted_retries_propagate_the_build_exit_code(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    command = (
        f"n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; "
        'echo "429 toomanyrequests" >&2; exit 7'
    )

    result = run_script("sh", "-c", command, attempts="2")

    assert result.returncode == 7
    assert counter.read_text().strip() == "2"


def test_smoke_script_builds_through_the_retry_wrapper() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "retry-docker-build.sh" in text


def test_workflow_routes_every_build_through_the_retry_wrapper() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    invocations = [
        line
        for line in text.splitlines()
        if "retry-docker-build.sh" in line and not line.lstrip().startswith("#")
    ]
    assert len(invocations) == 1, (
        "the publish job is the only workflow build not already delegated to "
        "run-smoke.sh; adding another direct build would reintroduce an "
        "unprotected registry pull"
    )

    # The smoke job builds via run-smoke.sh, which already wraps the build, so no
    # job may invoke docker build/compose build directly.
    assert "docker build " not in text
    assert "compose build" not in text

    publish_job = text.split("\n  publish:", 1)[1]
    assert "docker buildx build" in publish_job
    # docker/build-push-action generated this attestation implicitly; the
    # hand-written buildx invocation has to keep producing it.
    assert "type=provenance" in publish_job


def test_workflow_does_not_build_the_image_twice() -> None:
    """A separate `docker` job duplicated the smoke job's compose build."""

    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\n  docker:\n" not in text
    assert "onestep-control-plane:ci" not in text


@pytest.mark.parametrize("pattern", ["toomanyrequests", "data limit exceeded"])
def test_wrapper_recognises_the_ecr_rate_limit_wording(pattern: str) -> None:
    text = SCRIPT.read_text(encoding="utf-8").lower()
    assert pattern in text
