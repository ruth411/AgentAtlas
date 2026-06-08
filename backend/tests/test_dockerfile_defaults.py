"""Packaging regression tests for the repo-root Docker image.

These tests pin security-sensitive container defaults that do not live
inside the Python package itself. The image is a first-class distribution
path, so changes to its env vars / default command deserve the same
regression coverage as CLI flags.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_dockerfile_defaults_strict_tool_lock_on() -> None:
    content = DOCKERFILE.read_text()
    assert "AYIRU_STRICT_TOOL_LOCK=1" in content, (
        "Docker image must default AYIRU_STRICT_TOOL_LOCK=1 so network-exposed "
        "container deployments reject unknown tool_ids unless the operator "
        "explicitly opts out."
    )


def test_dockerfile_default_cmd_keeps_auto_seed() -> None:
    content = DOCKERFILE.read_text()
    assert 'CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--auto-seed"]' in content, (
        "Docker image should keep the first-run auto-seed default so "
        "`docker run -p 8000:8000 ayiru` starts with a populated graph."
    )
