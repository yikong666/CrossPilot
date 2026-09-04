from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, cast


class Encoder(Protocol):
    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ) -> Any: ...


class EmbeddingService:
    """Lazy BGE-M3 adapter; injection keeps deterministic tests offline."""

    dimensions = 1024

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        encoder: Encoder | None = None,
    ) -> None:
        self.model_name = model_name
        self._encoder = encoder

    @property
    def encoder(self) -> Encoder:
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer

            self._encoder = cast(Encoder, SentenceTransformer(self.model_name))
        return self._encoder

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        normalized = [text.strip() for text in texts]
        if not normalized:
            return []
        if any(not text for text in normalized):
            raise ValueError("embedding text must not be empty")
        encoded = self.encoder.encode(
            normalized,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        vectors = cast(list[list[float]], encoded.tolist())
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"{self.model_name} must return {self.dimensions}-dimensional vectors"
                )
            norm = math.sqrt(sum(value * value for value in vector))
            if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
                raise ValueError("embedding vectors must be normalized")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed_entities(
        self, records: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        copied = [dict(record) for record in records]
        texts = [self.entity_text(record) for record in copied]
        vectors = self.embed(texts)
        return [
            {**record, "embedding_text": text, "embedding": vector}
            for record, text, vector in zip(copied, texts, vectors, strict=True)
        ]

    @staticmethod
    def entity_text(record: Mapping[str, Any]) -> str:
        aliases = record.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        values = [
            record.get("name"),
            record.get("title_en"),
            record.get("brand"),
            *cast(Iterable[object], aliases),
        ]
        return " | ".join(str(value).strip() for value in values if str(value or "").strip())
