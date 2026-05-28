"""Semantic embedding service for the query engine's re-rank step.

Wraps :mod:`fastembed` (ONNX-runtime; no torch dependency) around the
``BAAI/bge-small-en-v1.5`` model — 384-dim, ~130 MB on disk, CPU-only,
~10 ms per embedding once warm. The model loads lazily on first use
and is cached for the process lifetime.

Storage contract: embeddings are serialized as ``json.dumps(list[float])``
into the ``knowledge_claims.embedding`` TEXT column (migration 0017).
Loading is fast (~50 µs to parse a 384-float list); persistent storage
is portable across runtimes; no sqlite-vec dependency.

Used by:
- ``ClaimStore.create`` — compute + persist embedding on insert
- ``ClaimStore.backfill_embeddings`` — fill rows missing the column
- ``QueryEngine.ask`` — embed the question + re-rank candidates

Why fastembed over sentence-transformers: torch is ~2 GB on Linux; the
ONNX path is ~200 MB and starts faster. Same model weights, same
output."""

from __future__ import annotations

import json
import math
import threading
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from fastembed import TextEmbedding


_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBEDDING_DIM = 384


class EmbeddingService:
    """Process-singleton handle around fastembed's TextEmbedding.

    Constructing the service is cheap; the model only loads on the first
    ``embed`` call. Subsequent calls reuse the loaded instance. Thread-
    safe for concurrent reads after the first call."""

    _instance: "EmbeddingService | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._model: "TextEmbedding | None" = None
        self._model_lock = threading.Lock()

    @classmethod
    def get_default(cls) -> "EmbeddingService":
        """Return the process-wide singleton, lazily instantiated."""

        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure_model_loaded(self) -> "TextEmbedding":
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            # Lazy import: fastembed pulls in onnxruntime + numpy at
            # import time, which adds ~200 ms of import latency we
            # don't want to pay on every backend boot.
            from fastembed import TextEmbedding  # noqa: PLC0415

            self._model = TextEmbedding(model_name=_MODEL_NAME)
            return self._model

    def embed_one(self, text: str) -> list[float]:
        """Return the 384-dim embedding for ``text``."""

        model = self._ensure_model_loaded()
        # fastembed.embed yields a generator of np.ndarray; convert to
        # plain Python floats for JSON-safe serialization.
        for vec in model.embed([text]):
            return [float(v) for v in vec]
        raise RuntimeError("fastembed.embed yielded no vector")  # unreachable

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        """Batch variant — faster than calling :meth:`embed_one` in a loop
        because fastembed amortizes ONNX session overhead across the batch."""

        model = self._ensure_model_loaded()
        return [[float(v) for v in vec] for vec in model.embed(list(texts))]


def serialize_embedding(vec: list[float]) -> str:
    """Encode an embedding vector for persistence in the
    ``knowledge_claims.embedding`` TEXT column."""

    return json.dumps(vec, separators=(",", ":"))


def deserialize_embedding(serialized: str | None) -> list[float] | None:
    """Decode the persisted form. Returns ``None`` when the column was
    null (claim hasn't been embedded yet) so the caller can fall through
    to lexical-only ranking for that row."""

    if not serialized:
        return None
    return json.loads(serialized)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain-Python cosine similarity. With 384-dim vectors and ~600
    claims this runs in <1 ms total — no numpy dependency for the hot
    path, no GPU, no per-query overhead beyond the question embedding."""

    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def claim_embedding_text(*, subject: str, statement: str) -> str:
    """The canonical text we embed per claim. Subject carries the tool /
    command identity (high signal); statement carries the answer body.
    Concatenating them gives the matcher both surfaces in one vector."""

    return f"{subject}\n\n{statement}".strip()


__all__ = [
    "EmbeddingService",
    "cosine_similarity",
    "claim_embedding_text",
    "serialize_embedding",
    "deserialize_embedding",
]
