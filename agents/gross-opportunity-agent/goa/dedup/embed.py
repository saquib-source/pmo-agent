"""
Embedding-based similarity for genuinely ambiguous dedup candidates.
Only called when fuzzy match puts a pair in the ambiguous band.
Gap: confirm embedding model endpoint via Vertex AI Model Garden.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)

_EMBED_MODEL = "text-embedding-004"  # Calibrated — confirm with Manmeet


def _embed_client():
    from google.cloud import aiplatform  # type: ignore
    return aiplatform.TextEmbeddingModel.from_pretrained(_EMBED_MODEL)


async def embedding_similarity(text_a: str, text_b: str) -> float:
    """Return cosine similarity in [0, 1] between the two texts."""
    try:
        model = _embed_client()
        embs = model.get_embeddings([text_a, text_b])
        a, b = embs[0].values, embs[1].values
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
    except Exception as e:
        log.warning("Embedding similarity failed: %s", e)
        return 0.0


async def is_same_project(opp_a: Any, opp_b: dict) -> bool:
    """Compare two records using embeddings. Returns True if they represent the same project."""
    text_a = f"{opp_a.project_name or ''} {opp_a.address.city or ''} {opp_a.owner or ''}"
    text_b = f"{opp_b.get('project_name') or ''} {opp_b.get('city') or ''} {opp_b.get('owner') or ''}"
    similarity = await embedding_similarity(text_a, text_b)
    log.debug("Embedding similarity %.2f between '%s' and '%s'", similarity, text_a[:60], text_b[:60])
    return similarity >= 0.88  # Calibrated threshold
