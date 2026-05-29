"""Docker probe — 45 questions across CLI, config, build, recipes, errors."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

os.environ.setdefault(
    "AYIRU_DATABASE_URL",
    f"sqlite:///{Path(__file__).resolve().parents[2] / 'backend' / 'ayiru_v0.2_bulk.db'}",
)

from app.services.claim_store import ClaimStore  # noqa: E402
from app.services.query_engine import QueryEngine  # noqa: E402

QUESTIONS = [
    # CLI (15)
    ("docker-cli", "how do i run a container"),
    ("docker-cli", "how to exec into a running container"),
    ("docker-cli", "view container logs"),
    ("docker-cli", "list running containers"),
    ("docker-cli", "stop a container gracefully"),
    ("docker-cli", "remove a stopped container"),
    ("docker-cli", "inspect a container"),
    ("docker-cli", "copy files from container to host"),
    ("docker-cli", "build an image from a Dockerfile"),
    ("docker-cli", "tag and push an image"),
    ("docker-cli", "list local images"),
    ("docker-cli", "remove an image"),
    ("docker-cli", "create a volume"),
    ("docker-cli", "list networks"),
    ("docker-cli", "prune unused resources"),
    # Config (6)
    ("docker-config", "what is FROM in Dockerfile"),
    ("docker-config", "COPY vs ADD in Dockerfile"),
    ("docker-config", "compose file services section"),
    ("docker-config", "define a network in compose"),
    ("docker-config", "named volume in compose"),
    ("docker-config", "daemon.json configuration"),
    # Build (6)
    ("docker-build", "what is buildkit"),
    ("docker-build", "multi-stage docker build"),
    ("docker-build", "build cache backends"),
    ("docker-build", "multi-platform docker images"),
    ("docker-build", "build secrets in docker"),
    ("docker-build", "dockerfile best practices"),
    # Recipes (9)
    ("docker-recipes", "run a one-shot container"),
    ("docker-recipes", "named long-running container"),
    ("docker-recipes", "multi-stage Dockerfile example"),
    ("docker-recipes", "compose up a stack"),
    ("docker-recipes", "use named volumes for persistence"),
    ("docker-recipes", "free up disk space from docker"),
    ("docker-recipes", "set CPU and memory limits"),
    ("docker-recipes", "add a healthcheck"),
    ("docker-recipes", "run container as non-root"),
    # Errors (9)
    ("docker-errors", "permission denied on docker.sock"),
    ("docker-errors", "cannot connect to docker daemon"),
    ("docker-errors", "pull access denied repository"),
    ("docker-errors", "image not found"),
    ("docker-errors", "port is already allocated"),
    ("docker-errors", "no space left on device"),
    ("docker-errors", "container exited with 137"),
    ("docker-errors", "exec format error"),
    ("docker-errors", "you have reached your pull rate limit"),
]


def main() -> int:
    useful = weak = miss = 0
    rows = []
    try:
        store = ClaimStore()
        engine = QueryEngine(store)
        for tool, q in QUESTIONS:
            resp = engine.ask(question=q, limit=1, tool_id_hint=tool)
            answers = resp.answers or []
            if not answers:
                rows.append((tool, q, "MISS", 0.0, ""))
                miss += 1
                continue
            top = answers[0]
            conf = float(top.confidence or 0.0)
            ver = top.verification_level
            useful_flag = conf >= 0.6 and ver != "L0_unverified"
            snippet = (top.statement or "")[:60]
            if useful_flag:
                rows.append((tool, q, "USEFUL", conf, snippet))
                useful += 1
            else:
                rows.append((tool, q, "WEAK", conf, snippet))
                weak += 1
    finally:
        pass

    for tool, q, verdict, conf, snippet in rows:
        print(f"  [{verdict:6}] {tool:15} c={conf:.2f}  {q}")
        if snippet:
            print(f"           → {snippet}")
    print()
    print(f"docker probe: {useful}/{len(QUESTIONS)} USEFUL, {weak} WEAK, {miss} MISS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
