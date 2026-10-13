from __future__ import annotations

import sys

from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.judge.llm_backend import BACKEND_LABELS


def cmd_llm_findings(args):
    """Lists stored typed review decisions at or above a backend-specific score threshold."""
    if not 0.0 <= args.min_confidence <= 1.0:
        print("--min-confidence must be between 0.0 and 1.0.")
        sys.exit(2)

    config = Config.load(args.config)
    backend = getattr(args, "backend", None) or config.llm_judge.default_backend
    db = DocAuditDB(config.get_db_path())
    classifications = args.classification
    if args.all_outcomes:
        classifications = None
    elif not classifications:
        classifications = ["confirmed_violation", "possible_violation"]
    rows = db.get_judge_evaluations(
        min_confidence=args.min_confidence,
        classifications=classifications,
        run_type=args.run_type,
        limit=args.limit if args.limit > 0 else None,
        backend=backend,
    )
    db.close()

    backend_label = BACKEND_LABELS.get(backend, backend)
    confidence_metric = "probability concentration" if backend == "clef-flash" else "confidence"
    score_percent = f"{args.min_confidence:.0%}"
    print(
        f"LLM review evaluations for {backend_label} with {confidence_metric} >= "
        f"{score_percent}: showing {len(rows)}"
    )
    if not rows:
        print(
            "No matching stored evaluations. Run a review with this backend to populate its index."
        )
        sys.exit(0)
    print(
        "Typed decision backends do not store rationale or citations; review each listed section manually."
    )
    if backend == "clef-flash":
        print(
            "Clef Flash probability concentration describes how concentrated its choice probabilities "
            "are; it is not the probability that the selected answer is correct."
        )
    headers = [
        confidence_metric,
        "outcome",
        "severity",
        "review",
        "record",
        "check",
        "location",
        "covered files",
        "backend",
        "generated at",
    ]
    print("| " + " | ".join(headers) + " |")
    print("| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        cells = [
            f"{row['confidence']:.0%}",
            row["classification"],
            row["severity"] or "-",
            row["run_type"],
            row["item_id"],
            row["check_id"],
            row["location"],
            ", ".join(row["covered_files"]),
            row["backend"],
            row["generated_at"],
        ]
        safe_cells = [str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells]
        print("| " + " | ".join(safe_cells) + " |")
    sys.exit(0)
