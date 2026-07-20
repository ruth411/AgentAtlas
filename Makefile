# Ayiru catalog build & maintenance targets.
#
# The full multi-family prose catalog (`backend/ayiru_v0.2_bulk.db`) is built
# from a wide docs crawl plus per-family seed scripts and is *not* byte-for-byte
# reproducible (it depends on live, changing documentation sites). For that
# artifact the source of truth is the Git LFS snapshot of the bulk DB; these
# targets rebuild the deterministic pieces and refresh that snapshot.
#
# The structured `gh` catalog *is* deterministic: it runs the local `gh --help`
# and parses typed argv/flag data. `make catalog-structured` rebuilds it from
# scratch into a fresh schema DB.

PY := ./backend/.venv/bin/python
BULK_DB := backend/ayiru_v0.2_bulk.db
STRUCTURED_DB := backend/ayiru_structured_gh.db
SLIM_DB := mcp/ayiru_mcp/data/catalog.db
TOOL_FAMILIES := gh

.PHONY: help catalog-structured slim-catalog audit purge reingest catalog-snapshot test mcp-release-check

help:
	@echo "Ayiru catalog targets:"
	@echo "  catalog-structured  Rebuild the deterministic structured gh catalog into $(STRUCTURED_DB)"
	@echo "  slim-catalog        Rebuild the bundled wheel catalog ($(SLIM_DB)) from $(BULK_DB)"
	@echo "  audit               Run the catalog quality audit against $(BULK_DB)"
	@echo "  purge               Demote chrome-contaminated accepted claims in $(BULK_DB)"
	@echo "  reingest            Re-fetch + re-sanitize junk claims in $(BULK_DB)"
	@echo "  catalog-snapshot    Refresh the Git LFS snapshot of $(BULK_DB)"
	@echo "  test                Run the backend + SDK test suites"
	@echo "  mcp-release-check   Rebuild the bundled MCP catalog and run MCP release verification"

# Deterministic: provision a fresh schema-only DB, then ingest structured gh
# from real `gh --help` output. No network, no prose crawl.
catalog-structured:
	rm -f $(STRUCTURED_DB)
	AYIRU_DATABASE_URL="sqlite:///$(abspath $(STRUCTURED_DB))" $(PY) -c "from alembic import command; from app.services.alembic_config import make_alembic_config; command.upgrade(make_alembic_config('sqlite:///$(abspath $(STRUCTURED_DB))'), 'head')"
	GH_NO_UPDATE_NOTIFIER=1 $(PY) tools/scripts/structured_ingest_gh.py --database "sqlite:///$(abspath $(STRUCTURED_DB))"
	$(PY) tools/scripts/audit_catalog_quality.py --database $(STRUCTURED_DB)

# Rebuild the slim catalog shipped inside the ayiru-mcp wheel.
slim-catalog:
	mkdir -p $(dir $(SLIM_DB))
	# Absolute paths: alembic's env resolution changes the working directory,
	# so a relative --output cannot be opened during the schema-migrate step.
	$(PY) tools/scripts/build_slim_catalog.py \
		--source $(abspath $(BULK_DB)) \
		--output $(abspath $(SLIM_DB)) \
		--tool-families "$(TOOL_FAMILIES)" \
		--structured-only

audit:
	$(PY) tools/scripts/audit_catalog_quality.py --database $(BULK_DB)

purge:
	$(PY) tools/scripts/purge_contaminated_claims.py --database $(BULK_DB)

reingest:
	GH_NO_UPDATE_NOTIFIER=1 $(PY) tools/scripts/reingest_junk_claims.py --db $(BULK_DB)

# Re-stage the bulk DB into Git LFS so a clone gets a functional catalog
# without re-running the (non-deterministic) crawl.
catalog-snapshot:
	git add $(BULK_DB)
	@echo "Staged $(BULK_DB) for LFS. Review with 'git status' then commit."

test:
	cd backend && .venv/bin/python -m pytest -q
	./backend/.venv/bin/pytest clients/python/tests -q

mcp-release-check:
	cd backend && .venv/bin/python -m pytest -q tests/test_mcp_server.py tests/test_build_slim_catalog.py
	PYTHONPATH=$(abspath backend):$(abspath mcp) $(PY) -m pytest mcp/tests -q
	$(PY) tools/scripts/rebuild_structured_product.py --bundle-output /tmp/ayiru-mcp-release-catalog.db --skip-coverage --skip-freshness
	$(PY) tools/scripts/smoke_mcp_wheel.py --catalog /tmp/ayiru-mcp-release-catalog.db
	./backend/.venv/bin/pytest clients/python/tests -q
