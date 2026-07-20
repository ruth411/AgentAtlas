from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "smoke_product",
    ROOT / "tools" / "scripts" / "smoke_product.py",
)
smoke_module = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
SPEC.loader.exec_module(smoke_module)


def test_run_smoke_queries_against_a_temporary_copy(monkeypatch, tmp_path: Path) -> None:
    database_path = tmp_path / "bulk.db"
    catalog_path = tmp_path / "catalog.db"
    database_path.write_bytes(b"bulk")
    catalog_path.write_bytes(b"catalog")

    monkeypatch.setattr(smoke_module, "_table_count", lambda *_args, **_kwargs: 1)

    seen_urls: list[str] = []

    class FakeClaimStore:
        def __init__(self, database_url: str):
            seen_urls.append(database_url)

    class FakeQueryEngine:
        def __init__(self, _store):
            pass

        def resolve_subject(self, **_kwargs):
            return type(
                "Resolution",
                (),
                {"matches": [type("Match", (), {"subject_id": "gh-pr-create"})()]},
            )()

        def get_capabilities(self, **_kwargs):
            return type(
                "Capabilities",
                (),
                {
                    "capabilities": [
                        type(
                            "Capability",
                            (),
                            {"source": "structured", "capability_id": "cap-1"},
                        )()
                    ]
                },
            )()

        def get_effects(self, **_kwargs):
            return type(
                "Effects",
                (),
                {"effects": [type("Effect", (), {"effect_id": "eff-1"})()]},
            )()

        def resolve_action(self, **_kwargs):
            return type(
                "Action",
                (),
                {"top_capability": type("Capability", (), {"capability_id": "cap-1"})()},
            )()

    monkeypatch.setattr(smoke_module, "ClaimStore", FakeClaimStore)
    monkeypatch.setattr(smoke_module, "QueryEngine", FakeQueryEngine)

    result = smoke_module.run_smoke(
        database_path=database_path,
        catalog_path=catalog_path,
    )

    assert result["bulk_subject_id"] == "gh-pr-create"
    assert result["bulk_cap"] == "cap-1"
    assert result["bulk_effects"] == 1
    assert result["bundle_subject_id"] == "gh-pr-create"
    assert result["bundle_cap"] == "cap-1"
    assert result["bundle_action"] == "cap-1"
    assert seen_urls == [
        f"sqlite:///{database_path}",
        f"sqlite:///{catalog_path}",
    ]
