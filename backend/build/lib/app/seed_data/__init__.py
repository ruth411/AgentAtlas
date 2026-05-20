"""Stage 14: bundled demo dataset.

The canonical seed artifacts live at `data/seed_artifacts/` in the repo
root. These copies under `app/seed_data/artifacts/` ship inside the wheel
so a clean `pip install agentatlas && agentatlas seed --reset` succeeds
without a checkout. `tests/test_bundled_seed_in_sync.py` enforces the
two trees stay byte-identical.
"""
