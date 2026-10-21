"""Text embeddings: local fastembed (ONNX on the CPU, no API, no PyTorch)."""

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

import numpy as np

from scis_help.common import paths, tls
from scis_help.common.config import Settings

if TYPE_CHECKING:
    from fastembed import TextEmbedding

_BATCH_SIZE = 64


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        """(n, dim) float32, L2-normalised."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """(dim,) float32, L2-normalised."""
        ...


def normalise(vectors: np.ndarray) -> np.ndarray:
    """Rows scaled to unit length (zero rows stay zero), as float32."""
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return (vectors / np.where(norms == 0, 1.0, norms)).astype(np.float32)


class FastEmbedder:
    """fastembed's TextEmbedding, loaded on first use. The first load downloads the model
    (~70 MB, from Hugging Face) into ``paths.model_cache_dir()``."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._model: TextEmbedding | None = None
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            from fastembed import TextEmbedding

            for model in TextEmbedding.list_supported_models():
                if model["model"] == self.model_id:
                    self._dim = int(model["dim"])
                    break
            else:
                raise ValueError(f"fastembed does not support the model {self.model_id!r}")
        return self._dim

    def _load(self) -> "TextEmbedding":
        if self._model is None:
            # huggingface_hub makes its own HTTPS client for the download: OS trust store first.
            tls.use_system_trust_for_downloads()
            # Windows without Developer Mode has no symlinks; the cache works, only louder.
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from fastembed import TextEmbedding

            cache = paths.model_cache_dir()
            cache.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(model_name=self.model_id, cache_dir=str(cache))
        return self._model

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = list(self._load().passage_embed(list(texts), batch_size=_BATCH_SIZE))
        return normalise(np.vstack(vectors))

    def embed_query(self, text: str) -> np.ndarray:
        vector = next(iter(self._load().query_embed(text)))
        return normalise(np.asarray(vector))


def get_embedder(settings: Settings) -> Embedder:
    return FastEmbedder(settings.index.embed_model)
