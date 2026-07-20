"""Fresh-install smoke for ayiru-core + ayiru-mcp wheels.

Builds local wheels from staged temporary copies of `core/` and `mcp/`,
optionally swaps in a rebuilt bundled catalog for the staged MCP source,
downloads third-party runtime wheels into a temporary wheelhouse, installs
`ayiru-mcp` into a fresh runtime venv, then verifies:

1. `ayiru-mcp` starts and speaks MCP over stdio.
2. `initialize`, `tools/list`, and a public `resolve_subject` call succeed.
3. `ayiru_mcp` and `app.services.*` import from the runtime env's
   site-packages, not from the repo checkout.

This smoke is intentionally honest about scope: it verifies a real local wheel
build plus dependency-resolved install path. It requires PyPI access to fetch
third-party runtime wheels into the temporary wheelhouse.
"""

from __future__ import annotations

import argparse
from email import message_from_bytes
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile

from packaging.requirements import Requirement


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "core"
MCP_DIR = REPO_ROOT / "mcp"
DEFAULT_CATALOG = MCP_DIR / "ayiru_mcp" / "data" / "catalog.db"
INTERNAL_PROJECTS = {"ayiru-core", "ayiru-mcp"}
EXPECTED_TOOLS = [
    "resolve_subject",
    "get_subject_spec",
    "get_capabilities",
    "get_constraints",
    "get_effects",
    "resolve_action",
    "get_workflow_plan",
]


def _run(argv: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def _stage_package_tree(source_dir: Path, staging_root: Path) -> Path:
    staged = staging_root / source_dir.name
    shutil.copytree(source_dir, staged)
    return staged


def _build_local_wheels(*, wheelhouse: Path, catalog_path: Path) -> list[Path]:
    with tempfile.TemporaryDirectory(prefix="ayiru-mcp-wheel-src-") as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        staged_core = _stage_package_tree(CORE_DIR, tmp_dir)
        staged_mcp = _stage_package_tree(MCP_DIR, tmp_dir)
        staged_catalog = staged_mcp / "ayiru_mcp" / "data" / "catalog.db"
        shutil.copy2(catalog_path, staged_catalog)

        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(wheelhouse),
                str(staged_core),
            ]
        )
        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(wheelhouse),
                str(staged_mcp),
            ]
        )

    built = sorted(wheelhouse.glob("*.whl"))
    if len(built) < 2:
        raise RuntimeError(f"Expected ayiru-core and ayiru-mcp wheels, found: {[path.name for path in built]}")
    return built


def _wheel_requirements(wheel_path: Path) -> list[str]:
    with ZipFile(wheel_path) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = message_from_bytes(archive.read(metadata_name))
    return [value.strip() for value in metadata.get_all("Requires-Dist", [])]


def _collect_runtime_requirements(wheel_paths: list[Path]) -> list[str]:
    seen: set[str] = set()
    requirements: list[str] = []
    for wheel_path in wheel_paths:
        for requirement_text in _wheel_requirements(wheel_path):
            requirement = Requirement(requirement_text)
            if requirement.marker is not None and "extra" in str(requirement.marker):
                continue
            normalized = requirement.name.lower().replace("_", "-")
            if normalized in INTERNAL_PROJECTS or requirement_text in seen:
                continue
            seen.add(requirement_text)
            requirements.append(str(requirement))
    return requirements


def _download_runtime_wheels(*, wheelhouse: Path, requirements: list[str]) -> None:
    if not requirements:
        return
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheelhouse),
            "--only-binary=:all:",
            *requirements,
        ]
    )


def _validate_mcp_probe(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 3:
        raise RuntimeError(f"Expected 3 MCP replies, got {len(lines)}: {lines!r}")
    initialize = json.loads(lines[0])
    tools_list = json.loads(lines[1])
    resolve_subject = json.loads(lines[2])

    tool_names = [tool["name"] for tool in tools_list["result"]["tools"]]
    if initialize["result"]["serverInfo"]["name"] != "Ayiru":
        raise RuntimeError(f"Unexpected MCP server name: {initialize}")
    if tool_names != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected advertised MCP tool list: {tool_names}")

    probe = resolve_subject["result"]["structuredContent"]
    matches = probe.get("matches") or []
    if not matches:
        raise RuntimeError("resolve_subject probe returned no bundled matches.")

    return {
        "tool_count": len(tool_names),
        "first_tool": tool_names[0],
        "resolved_subject": matches[0]["subject_id"],
    }


def run_smoke(*, catalog_path: Path) -> dict[str, object]:
    catalog_path = catalog_path.resolve()
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Bundled catalog not found: {catalog_path}")

    with tempfile.TemporaryDirectory(prefix="ayiru-mcp-wheel-") as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        runtime_env = tmp_dir / "runtime-env"
        wheelhouse = tmp_dir / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=True)

        wheels = _build_local_wheels(wheelhouse=wheelhouse, catalog_path=catalog_path)
        runtime_requirements = _collect_runtime_requirements(wheels)
        _download_runtime_wheels(
            wheelhouse=wheelhouse,
            requirements=runtime_requirements,
        )

        _run([sys.executable, "-m", "venv", str(runtime_env)])
        runtime_python = runtime_env / "bin" / "python"
        runtime_pip = runtime_env / "bin" / "pip"
        _run(
            [
                str(runtime_pip),
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "ayiru-mcp",
            ]
        )

        import_check = _run(
            [
                str(runtime_python),
                "-c",
                (
                    "import json, ayiru_mcp, app.services.claim_store as cs; "
                    "print(json.dumps({'ayiru_mcp': ayiru_mcp.__file__, 'claim_store': cs.__file__}))"
                ),
            ]
        )
        origins = json.loads(import_check.stdout.strip())
        if str(REPO_ROOT) in origins["ayiru_mcp"] or str(REPO_ROOT) in origins["claim_store"]:
            raise RuntimeError(f"Wheel smoke imported Ayiru modules from the repo checkout: {origins}")

        server = subprocess.Popen(
            [str(runtime_env / "bin" / "ayiru-mcp")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            payload = "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "resolve_subject",
                                "arguments": {
                                    "subject_hint": "gh pr create",
                                    "limit": 1,
                                },
                            },
                        }
                    ),
                ]
            ) + "\n"
            stdout, stderr = server.communicate(input=payload, timeout=10)
        finally:
            if server.poll() is None:
                server.kill()

        if server.returncode != 0:
            raise RuntimeError(f"ayiru-mcp wheel smoke exited {server.returncode}: {stderr}")

        probe = _validate_mcp_probe(stdout)

        return {
            "wheels": sorted(path.name for path in wheels),
            "runtime_requirements": runtime_requirements,
            "tool_count": probe["tool_count"],
            "first_tool": probe["first_tool"],
            "resolved_subject": probe["resolved_subject"],
            "catalog": str(catalog_path),
            "ayiru_mcp_origin": origins["ayiru_mcp"],
            "claim_store_origin": origins["claim_store"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        default=str(DEFAULT_CATALOG),
        help="Catalog DB to stage into the temporary ayiru-mcp source tree before building wheels.",
    )
    args = parser.parse_args()

    result = run_smoke(catalog_path=Path(args.catalog))
    print(
        "Wheel smoke OK:",
        f"wheels={','.join(result['wheels'])}",
        f"deps={','.join(result['runtime_requirements'])}",
        f"tool_count={result['tool_count']}",
        f"first_tool={result['first_tool']}",
        f"resolved_subject={result['resolved_subject']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
