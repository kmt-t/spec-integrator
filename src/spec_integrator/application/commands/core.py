from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from spec_integrator.application.commands.common import _load_and_parse_all
from spec_integrator.application.utils import _rel_path, _write_text
from spec_integrator.config import Config
from spec_integrator.judge import (
    RiskAssessor,
)


def cmd_init(args):
    target = Path("spec-integrator.yaml")
    if target.exists():
        print(f"[Error] '{target}' already exists.")
        sys.exit(1)

    template = r"""version: "1.0"

project:
  name: "My Specification Project"
  docs_root: "docs"
  cache_db: ".spec-integrator/doc_cache.db"

tiers:
  - tier: 0
    name: "Requirements"
    path_pattern: 'requires/.*\.md'
    description: "System Requirements"

  - tier: 1
    name: "Core"
    path_pattern: 'components/tier1_.*\.md'
    description: "Core System Components"

  - tier: 2
    name: "Runtime"
    path_pattern: 'components/tier2_.*\.md'
    description: "Runtime & Execution Engine"

  - tier: 3
    name: "Platform"
    path_pattern: 'components/tier3_.*\.md'
    description: "Platform Abstraction & Drivers"

  - tier: "meta"
    name: "Architecture & Plans"
    path_pattern: '(architecture|plans)/.*\.md'
    description: "Architecture & Plans"

formal_verification:
  model_dir_name: "formal"
  tag: "{VERIFY_FORMAL}"
  timeout_seconds: 30
  require_contract: true
  check_vacuity: true
  check_reachability: true
  check_nondeterminism: true
  min_states: 4

llm_judge:
  tag: "{VERIFY_LLM}"
  default_backend: "clef-flash"
  backends:
    jev:
      api_key_env: "OPENROUTER_API_KEY"
      endpoint: "https://openrouter.ai/api/alpha/decisions"
      model: "typesafe/jev-1.13"
    clef-flash:
      endpoint: "http://localhost:11434/v1/systemone"
      model: "clef-flash"
      requires_api_key: false
      context_window_tokens: 65536

embeddings:
  endpoint: "http://localhost:11434"

terminology:
  embedding_model: "qwen3-embedding"

evidence:
  enabled: true
  metric_severity: "WARNING"
  ignore_artifact_refs: []

obligation:
  enabled: true
  require_assessment: true
  require_judge: true
  risk_threshold: 4
"""
    target.write_text(template, encoding="utf-8")
    print(f"✔ Created '{target}'.")
    sys.exit(0)


def cmd_graph(args):
    config = Config.load(args.config)
    _documents, graph, db, _docs_root = _load_and_parse_all(config)
    fmt = args.format.lower()
    if fmt == "json":
        content = json.dumps(graph.to_dict(), indent=2, ensure_ascii=False)
    else:
        content = graph.to_mermaid()

    if args.out:
        out_p = _write_text(args.out, content)
        print(f"✔ Graph saved to {_rel_path(out_p)}")
    else:
        print(content)

    db.close()
    sys.exit(0)


def cmd_risk(args):
    """Evaluates requirement/design keywords complexity and design risk via LLM."""
    config = Config.load(args.config)
    documents, graph, db, _docs_root = _load_and_parse_all(config)
    subgraphs = graph.extract_item_subgraphs()
    assessor = RiskAssessor(config)
    used_backend = args.backend or config.llm_judge.default_backend
    print(f"Running Content Complexity & Risk Assessment (backend: {used_backend})...")
    report = assessor.assess_subgraphs(
        subgraphs,
        documents,
        backend=args.backend,
        model=args.model,
        max_keywords=args.max_keywords,
        exhaustive=args.exhaustive,
        min_references=args.min_references,
    )

    db.replace_risk_assessments([asdict(a) for a in report.assessments], used_backend)
    db.set_assessed_doc_hashes("risk_assessment", {d.file_path: d.content_hash for d in documents})
    db.commit()
    db.close()

    print(f"\nAssessment finished. Evaluated {report.total_evaluated} keyword(s).")
    print(f"  - High risk (>= {config.obligation.risk_threshold}/5): {report.high_risk_count}")
    print("Scores recorded in the cache DB; see 'check' report § Risk Assessment Detail.")
    sys.exit(0)
