from __future__ import annotations

import importlib.util
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "smoke_mcp_wheel",
    ROOT / "tools" / "scripts" / "smoke_mcp_wheel.py",
)
smoke_module = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
SPEC.loader.exec_module(smoke_module)


def _write_wheel(path: Path, metadata: str) -> None:
    dist_info = path.stem.replace("-", "_") + ".dist-info"
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)


def test_stage_package_tree_keeps_repo_source_untouched(tmp_path: Path) -> None:
    source = tmp_path / "mcp"
    (source / "ayiru_mcp" / "data").mkdir(parents=True)
    original_catalog = source / "ayiru_mcp" / "data" / "catalog.db"
    original_catalog.write_text("original", encoding="utf-8")

    staged = smoke_module._stage_package_tree(source, tmp_path / "staged")
    staged_catalog = staged / "ayiru_mcp" / "data" / "catalog.db"
    staged_catalog.write_text("rebuilt", encoding="utf-8")

    assert original_catalog.read_text(encoding="utf-8") == "original"
    assert staged_catalog.read_text(encoding="utf-8") == "rebuilt"


def test_collect_runtime_requirements_excludes_internal_packages(tmp_path: Path) -> None:
    core_wheel = tmp_path / "ayiru_core-0.1.0-py3-none-any.whl"
    mcp_wheel = tmp_path / "ayiru_mcp-0.2.1-py3-none-any.whl"

    _write_wheel(
        core_wheel,
        "\n".join(
            [
                "Metadata-Version: 2.1",
                "Name: ayiru-core",
                "Version: 0.1.0",
                "Requires-Dist: pydantic<3.0,>=2.8",
                "Requires-Dist: sqlalchemy<3.0,>=2.0",
                "",
            ]
        ),
    )
    _write_wheel(
        mcp_wheel,
        "\n".join(
            [
                "Metadata-Version: 2.1",
                "Name: ayiru-mcp",
                "Version: 0.2.1",
                "Requires-Dist: ayiru-core<0.2,>=0.1",
                "Requires-Dist: pydantic<3.0,>=2.8",
                "Requires-Dist: fastembed<1.0,>=0.8; extra == 'semantic'",
                "",
            ]
        ),
    )

    requirements = smoke_module._collect_runtime_requirements([core_wheel, mcp_wheel])

    assert requirements == [
        "pydantic<3.0,>=2.8",
        "sqlalchemy<3.0,>=2.0",
    ]


def test_validate_mcp_probe_accepts_public_tool_call_result() -> None:
    stdout = "\n".join(
        [
            '{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"Ayiru"}}}',
            (
                '{"jsonrpc":"2.0","id":2,"result":{"tools":['
                '{"name":"resolve_subject"},'
                '{"name":"get_subject_spec"},'
                '{"name":"get_capabilities"},'
                '{"name":"get_constraints"},'
                '{"name":"get_effects"},'
                '{"name":"resolve_action"},'
                '{"name":"get_workflow_plan"}'
                "]}}"
            ),
            (
                '{"jsonrpc":"2.0","id":3,"result":{"structuredContent":{'
                '"matches":[{"subject_id":"gh-pr-create"}]'
                "}}}"
            ),
        ]
    )

    result = smoke_module._validate_mcp_probe(stdout)

    assert result == {
        "tool_count": 7,
        "first_tool": "resolve_subject",
        "resolved_subject": "gh-pr-create",
    }
