from __future__ import annotations

import math
from typing import TYPE_CHECKING

from spec_integrator.judge.llm_backend import call_sakura_embeddings

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity between two float vectors."""
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


class SectionTopicIndexer:
    """Handles embedding generation and cross-document topic similarity indexing for sections."""

    def __init__(self, config: Config):
        self.config = config

    def index_section_embeddings(
        self,
        db: DocAuditDB,
        model: str | None = None,
        batch_size: int | None = None,
    ) -> int:
        """Fetches embeddings from Sakura AI for unembedded/changed sections and saves them."""
        selected_model = model or getattr(
            self.config.semantic_topic, "embedding_model", "multilingual-e5-large"
        )
        b_size = batch_size or getattr(self.config.semantic_topic, "batch_size", 16)

        unembedded = db.get_unembedded_sections(selected_model)
        if not unembedded:
            return 0

        total = len(unembedded)
        print(f"  Embedding {total} section(s) in batches of {b_size}...", flush=True)

        total_embedded = 0
        for i in range(0, total, b_size):
            batch_secs = unembedded[i : i + b_size]
            batch_texts = [f"{hd}\n{body[:350]}".strip() for _sid, _fp, hd, body, _ch in batch_secs]
            try:
                vectors = call_sakura_embeddings(
                    self.config, batch_texts, model=selected_model, batch_size=b_size
                )
                records = [
                    (sec_id, fp, hd, ch, vec, selected_model)
                    for (sec_id, fp, hd, _body, ch), vec in zip(batch_secs, vectors, strict=False)
                ]
                db.insert_section_embeddings(records)
                db.commit()
                total_embedded += len(records)
                print(f"  Processed {total_embedded}/{total} sections...", flush=True)
            except Exception as e:
                print(f"[Warning] Failed batch {i}..{i + len(batch_secs)}: {e}", flush=True)
                break
        return total_embedded

    def compute_and_save_section_similarities(
        self,
        db: DocAuditDB,
        model: str | None = None,
        min_similarity: float | None = None,
    ) -> int:
        """Calculates pairwise cosine similarity across sections in different files and persists high-similarity pairs."""
        selected_model = model or getattr(
            self.config.semantic_topic, "embedding_model", "multilingual-e5-large"
        )
        threshold = (
            min_similarity
            if min_similarity is not None
            else getattr(self.config.semantic_topic, "similarity_threshold", 0.80)
        )

        all_sections = db.get_all_section_embeddings(selected_model)
        if len(all_sections) < 2:
            return 0

        # Filter sections with meaningful body content (skip pure headings / short boilerplate)
        meaningful_sections = []
        for s in all_sections:
            info = db.get_section_info(s["section_id"])
            if info:
                body = info.get("body_text", "")
                # Ignore very short sections (under 60 chars) to prevent boilerplate matching
                if len(body.strip()) >= 60:
                    meaningful_sections.append(s)

        similar_pairs: list[tuple[str, str, str, str, float, str]] = []

        # Compare sections across different files
        for i in range(len(meaningful_sections)):
            sec_a = meaningful_sections[i]
            file_a = sec_a["file_path"]
            vec_a = sec_a["vector"]

            for j in range(i + 1, len(meaningful_sections)):
                sec_b = meaningful_sections[j]
                file_b = sec_b["file_path"]

                # We focus on cross-file topic alignment (same file sections already share context)
                if file_a == file_b:
                    continue

                vec_b = sec_b["vector"]
                sim = cosine_similarity(vec_a, vec_b)
                if sim >= threshold:
                    similar_pairs.append(
                        (
                            sec_a["section_id"],
                            sec_b["section_id"],
                            file_a,
                            file_b,
                            round(sim, 4),
                            selected_model,
                        )
                    )

        # Sort descending by similarity and keep top matches to avoid DB bloat
        similar_pairs.sort(key=lambda p: p[4], reverse=True)
        max_pairs = getattr(self.config.semantic_topic, "max_pairs", 1000)
        similar_pairs = similar_pairs[:max_pairs]
        db.replace_section_similarities(similar_pairs)
        db.commit()
        return len(similar_pairs)
