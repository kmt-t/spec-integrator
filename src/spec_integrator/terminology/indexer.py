from __future__ import annotations

import math
from typing import TYPE_CHECKING

from spec_integrator.judge.llm_backend import call_sakura_embeddings

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity between two vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b, strict=False):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class TermIndexer:
    """Handles embedding generation and similarity index calculation for terms."""

    def __init__(self, config: Config):
        self.config = config

    def index_embeddings(
        self, db: DocAuditDB, model: str | None = None, batch_size: int = 32
    ) -> int:
        """Fetches embeddings from Sakura AI for unembedded terms and stores them."""
        selected_model = model or getattr(
            self.config.terminology, "embedding_model", "multilingual-e5-large"
        )
        unembedded = db.get_unembedded_terms(selected_model)
        if not unembedded:
            return 0

        try:
            vectors = call_sakura_embeddings(
                self.config, unembedded, model=selected_model, batch_size=batch_size
            )
            records = [
                (term, vec, selected_model) for term, vec in zip(unembedded, vectors, strict=False)
            ]
            db.insert_term_embeddings(records)
            db.commit()
            return len(records)
        except Exception as e:
            print(f"[Warning] Failed to generate term embeddings via Sakura AI: {e}")
            return 0

    def compute_and_save_similarities(
        self, db: DocAuditDB, model: str | None = None, min_similarity: float | None = None
    ) -> int:
        """Calculates pairwise cosine similarity across all embedded terms and persists high-similarity pairs."""
        selected_model = model or getattr(
            self.config.terminology, "embedding_model", "multilingual-e5-large"
        )
        threshold = (
            min_similarity
            if min_similarity is not None
            else getattr(self.config.terminology, "similarity_threshold", 0.80)
        )

        embeddings_dict = db.get_all_term_embeddings(selected_model)
        terms = sorted(embeddings_dict.keys())
        if len(terms) < 2:
            return 0

        similar_pairs: list[tuple[str, str, float]] = []

        # Compare pairs (term_a < term_b)
        for i in range(len(terms)):
            term_a = terms[i]
            vec_a = embeddings_dict[term_a]
            for j in range(i + 1, len(terms)):
                term_b = terms[j]
                # Avoid trivial self-like matches or exact casing match
                if term_a.lower() == term_b.lower():
                    continue

                vec_b = embeddings_dict[term_b]
                sim = cosine_similarity(vec_a, vec_b)
                if sim >= threshold:
                    similar_pairs.append((term_a, term_b, round(sim, 4)))

        # Sort by similarity descending
        similar_pairs.sort(key=lambda p: p[2], reverse=True)
        similar_pairs = similar_pairs[:500]
        db.replace_term_similarities(similar_pairs)
        db.commit()
        return len(similar_pairs)
