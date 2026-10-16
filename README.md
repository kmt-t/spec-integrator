# spec-integrator

`spec-integrator` is a general-purpose Python CLI for specification-driven software projects. It checks and visualizes document structure, requirement traceability, tier dependencies, formal models (pyModelChecking and mutation checks), Mermaid diagrams (`mermaidx`), WIT interfaces, and semantic consistency through risk assessment and LLM-as-a-Judge workflows.

---

## Main Features

- **Configuration-driven:** `spec-integrator.yaml` declares project-specific document tiers (Tier 0–3) and keyword sources.
- **Topological document graph (DocGraph):** Models files, heading sections, and requirement keywords as a directed graph and extracts traceability views and local subgraphs (`G_r`).
- **Eight strict quality gates (`check-doc`):**
  1. **Format:** Checks Markdown links and heading anchors, root-relative file link style, static typos, and Mermaid syntax and rendering with the embedded QuickJS-based `mermaidx` engine.
  2. **Traceability:** Finds undefined keyword references and unreferenced requirements.
  3. **Hierarchy:** Prevents dependencies that flow from a higher tier back into a lower tier.
  4. **Formal:** Runs pyModelChecking models tagged with `{VERIFY_FORMAL}`, rejects vacuous properties, and requires `guards=False` mutation checks to demonstrate that violations are reachable when protections are removed.
  5. **WIT:** Checks syntax and consistency of WIT definitions tagged with `{VERIFY_WIT}`.
  6. **Evidence:** Checks claims such as “verified,” “proved,” and “measured” against declared artifacts.
  7. **Obligation:** Finds verification work required by risk assessment that has not been completed.
  8. **Consistency:** Finds stale values and edits that were not propagated to dependent documents using the consistency baseline.

### Detect Skipped Verification (Automated Gates)

Quality gates report both failed checks and required checks that were never performed. The automated gate checks cover facts that can be determined mechanically, including file presence, hash matches, tags, and model structure. They do not claim to decide semantic LLM audit outcomes.

| Gate | Check | Rule code | Mechanical criterion |
| :--- | :--- | :--- | :--- |
| Format | Missing link target | `FMT-BROKEN-LINK` | The linked file exists. |
| Format | Missing heading anchor | `FMT-BROKEN-ANCHOR` | The target heading exists in the linked document. |
| Format | Invalid file-link style | `FMT-FILE-LINK-FORMAT` | The label is a basename; targets are project-root-relative; raw paths, absolute paths, and `../` links are absent. |
| Format | Static typo | `FMT-LEVENSHTEIN-TYPO` | Similarity checks find no likely typo in defined keywords or headings. |
| Format | Invalid Mermaid diagram | `FMT-INVALID-MERMAID` | `mermaidx` parses and renders the diagram. |
| Format | Missing Mermaid validator | `FMT-MERMAID-VALIDATOR-UNAVAILABLE` | The check fails if the validator cannot be imported. |
| Formal | Missing model | `FORMAL-MODEL-NOT-FOUND` | A `{VERIFY_FORMAL}` document has a linked model under `formal/`. |
| Formal | Missing model contract | `FORMAL-MODEL-NO-CONTRACT` | The model exposes `build_model()` and `properties()`. |
| Formal | Unsound model structure | `FORMAL-MODEL-UNSOUND` | All modeled states are reachable and the graph can represent branching. |
| Formal | Vacuous property | `FORMAL-PROPERTY-VACUOUS` | Each property has a modeled violation state. |
| Formal | Invalid property | `FORMAL-PROPERTY-INVALID` | Propositions occur in labels and mutation paths can reach violations. |
| Formal | Ambiguous backing model | `FORMAL-BACKING-AMBIGUOUS` | Shared models identify the documents they support with `BACKS`. |
| Evidence | Missing declared artifact | `EVID-DECLARED-FILE-MISSING` | Every path in an `evidence:` block exists. |
| Evidence | Missing formal evidence | `EVID-FORMAL-UNDECLARED` | `{VERIFY_FORMAL}` documents declare a `formal:` artifact. |
| Evidence | Missing WIT evidence | `EVID-WIT-UNDECLARED` | `{VERIFY_WIT}` documents declare a `wit:` artifact. |
| Evidence | Missing benchmark evidence | `EVID-BENCHMARK-UNDECLARED` | `{VERIFY_BENCHMARK}` documents declare a `benchmark:` artifact. |
| Evidence | Missing benchmark implementation | `EVID-BENCHMARK-MISSING` | A `{VERIFY_BENCHMARK}` document has a matching `benchmarks/*.py` file. |
| Evidence | Dangling artifact reference | `EVID-DANGLING-ARTIFACT-REF` | Referenced models, reports, and specifications exist. |
| Obligation | Missing risk assessment | `OBLIG-ASSESSMENT-MISSING` | Risk assessment data exists in the cache database. This is a warning. |
| Obligation | Missing assessment provenance | `OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN` | `run_metadata` records the assessment backend. |
| Obligation | Non-independent assessment | `OBLIG-ASSESSMENT-NOT-INDEPENDENT` | The assessment backend is not a prohibited self-referential checker. |
| Obligation | Partial assessment | `OBLIG-ASSESSMENT-PARTIAL` | All current keywords have risk-assessment results. This is a warning. |
| Obligation | Stale risk assessment | `OBLIG-ASSESSMENT-STALE` | The assessed document hashes match current files. This is a warning. |
| Obligation | Missing verification tag | `OBLIG-VERIFICATION-SKIPPED` | High-risk keywords have their required `{VERIFY_LLM}` tag. |
| Obligation | Missing or stale keyword audit | `OBLIG-JUDGE-MISSING`, `OBLIG-JUDGE-UNANCHORED`, `OBLIG-JUDGE-STALE`, `OBLIG-JUDGE-SKIPPED` | Stored audit results cover current document hashes and required targets. These are warnings. |
| Obligation | Failed keyword audit | `OBLIG-JUDGE-FAILED` | Stored results contain no failing verdict for referenced keywords. |
| Obligation | Missing or stale document audit | `OBLIG-DOC-JUDGE-MISSING`, `OBLIG-DOC-JUDGE-UNANCHORED`, `OBLIG-DOC-JUDGE-STALE`, `OBLIG-DOC-JUDGE-SKIPPED` | Document audits cover current hashes and required documents. These are warnings. |
| Obligation | Failed document audit | `OBLIG-DOC-JUDGE-FAILED` | The document's stored audit has no failing verdict. |
| Consistency | Duplicate keyword definition | `CONSIST-DUPLICATE-DEFINITION` | Each keyword has a single definition row. |
| Consistency | Symbol value drift | `CONSIST-SYMBOL-DRIFT` | Repeated symbols have the same normalized value. |
| Consistency | Stale dependent document | `CONSIST-COCHANGE-STALE` | Changes to definitions are propagated to references. |
| Consistency | Stale configured value | `CONSIST-STALE-VALUE` | Configured obsolete values are absent from documents. |

Missing, unanchored, stale, or skipped LLM work is reported as a warning. A recorded `FAIL` remains an error. Structural violations, such as a missing `{VERIFY_LLM}` tag for a high-risk keyword, remain errors regardless of whether an LLM audit ran.

`EVID-UNBACKED-CLAIM` and `EVID-UNSOURCED-MEASUREMENT` were removed because they had no implementation. Semantic checks for claims such as “verified” are part of the `llm-single-review` and `llm-keyword-review` prompts; they are not mechanical automated gate checks.

See the [formal model contract](docs/formal_model_contract.md) for requirements that formal models must satisfy.

### Detect Stale Propagation

`check-doc` compares documents with the consistency baseline in the cache database and reports edits that were not propagated.

```bash
spec-integrator check-doc   # Run checks and list stale dependent documents
spec-integrator build       # After resolving findings, refresh the document database and index
```

Dependencies are derived from existing `{Keyword}` traceability; no separate co-change declarations are needed.

- **Risk assessment and verification obligations (`risk`):** Scores design risk for requirement and design keywords. Keywords above the configured threshold require `{VERIFY_LLM}`. Results are stored in SQLite.
- **Terminology consistency (`llm-word`):** Combines TF-IDF terms, embedding similarity, and contextual judgments to find terminology drift and likely typos.
- **System One checklist audits (`llm-single-review`, `llm-keyword-review`):** Review document sections or keyword definition/reference pairs with typed questions and evidence. Jev returns confidence; Clef Flash returns probability concentration. Neither produces explanatory text or citations.
- **SQLite audit cache (`DocAuditDB`):** Stores document structure, hash-based incremental checks, risk results, classifications, and backend-specific scores.
- **CI and GitHub Actions:** Produces a single Markdown report with rule revisions, summary tables, findings, traceability, risk assessments, and recorded LLM results.

---

## Quick Start

### 1. Install with uv

```bash
cd tools/spec-integrator  # Or the project root
uv sync --system-certs
```

### 2. Initialize a configuration file

```bash
spec-integrator init
```
This creates a `spec-integrator.yaml` template in the current directory.

### 3. Build the document database and terminology index (`build`)

```bash
spec-integrator build --config spec-integrator.yaml
```

### 4. Format documents and run static quality gates (`format-doc`, `check-doc`)

```bash
# Format Markdown documents
spec-integrator format-doc --config spec-integrator.yaml

# Run all eight static gates with a clean state (CI-style)
spec-integrator check-doc --config spec-integrator.yaml --report report.md --clean
```
The standard run also checks Japanese prose with GiNZA and warns about long sentences or complex clause chains. Readability warnings are non-failing review candidates; they do not change the exit code. On success, `check-doc` exits with `0`; errors produce exit code `1` and details in `report.md`.

GiNZA does not rewrite prose. It analyzes sections split by headings, skips code blocks and headings, and checks prose in table cells. Adjust the three `prose_readability` settings in the project-root `spec-integrator.yaml` to change warning thresholds. These settings only affect warnings.

### 5. Format and check source code (`format-src`, `check-src`)

```bash
# Format Python with Ruff and C++ with clang-format
spec-integrator format-src --group all

# Check conventions and skipped work, including TODOs, empty functions, Any, and mutations
spec-integrator check-src --group all
```

The `python_pysim` group applies Ruff and the AST rules configured in the project-root `spec-integrator.yaml`. Its rule inventory is:

| Rule | Check |
| :--- | :--- |
| `forbid_typing_any` | Reject `typing.Any` and its aliases. |
| `forbid_object_type` | Reject `object` in annotations. |
| `forbid_non_none_union` | Allow only nullable `T | None` / `Optional[T]` unions. |
| `forbid_none_default_fill` | Reject nullable-value fallback expressions and replacing a `None` argument default at function entry. QA and benchmark paths are excluded in configuration. |
| `forbid_raise` | Reject explicit `raise` outside configured exception paths. |
| `forbid_rtti` | Reject runtime type and reflection inspection outside configured paths. |
| `forbid_test_backdoor` | Reject test-only hooks and configured test symbols in product code. |
| `forbid_required_nullable_arguments` | Reject required arguments declared nullable. |
| `forbid_string_members` | Reject runtime `str` members outside configured paths. |
| `dummy_pass` | Reject empty function bodies. |
| `todo_comment` | Report unresolved work markers. |

Separate checks in this group validate `assert` side effects, configured Tier import direction, and the PySIM unit-test suite. Built-in `dict`, `set`, and `list`, the `in` operator, and tuple reconstruction are not prohibited by this group's rules. The YAML configuration is the source of truth for enabled rules and exclusions.

The configured `complexity` check also runs for the `cpp` and `python_pysim` source groups. It uses lizard's Python API to reject functions above the configured cyclomatic-complexity threshold, ratchet listed baseline functions against increases, and warn when an allowlisted function reaches the threshold. Source scopes, exclusions, and baselines are configured in the project-root `spec-integrator.yaml`.

### 6. Visualize the DocGraph (`graph`)

```bash
# Print Mermaid to stdout
spec-integrator graph

# Write JSON output
spec-integrator graph -f json -o graph.json
```

### 7. Assess risk and derive verification obligations (`risk`)

```bash
# Without --backend, use llm_judge.default_backend from spec-integrator.yaml
# Results are cached and included in the Risk Assessment Detail section of check-doc reports
spec-integrator risk --config spec-integrator.yaml

# Use Jev only when explicitly requested
spec-integrator risk --backend jev
```
The default backend is local Ollama with Clef Flash (`clef-flash`). `--backend jev` uses Jev (`typesafe/jev-1.13`) through OpenRouter.

Clef Flash requires Ollama 0.35.1 or later. Pull it with `ollama pull clef-flash`. Set `context_window_tokens` to 65,536. System One request bodies are limited to 64 KiB.

To change the model context length, create an Ollama model with `PARAMETER num_ctx` in a Modelfile, then set `model` and `context_window_tokens` to match it.

Risk assessment samples up to four definition and four reference sections. Each section is limited to the smaller of `500 characters × (context_window_tokens / 8,192)` and `llm_judge.section_char_budget`. The default maximum is 4,000 characters per section. Content may be shortened further to fit the request; omitted ranges are recorded in the assessment summary.

Embeddings use local Ollama with `qwen3-embedding`. Set `OPENROUTER_API_KEY` only when using Jev. Configure Ollama endpoints with `llm_judge.backends.clef-flash.endpoint` and `embeddings.endpoint`. Run an LLM audit only when explicitly requested by the user.

### 8. Check terminology consistency (`llm-word`)

```bash
# Skip LLM contextual judgments; missing embeddings may still invoke local Ollama
spec-integrator llm-word --quick

# Include embeddings and LLM contextual judgments
spec-integrator llm-word
```

### 9. Run semantic audits (`llm-single-review`, `llm-keyword-review`)

```bash
# Audit one document or section
spec-integrator llm-single-review --file docs/components/tier1_core/os_scheduler.md

# Use local Clef Flash for one document
spec-integrator llm-single-review --file docs/components/tier1_core/os_scheduler.md --backend clef-flash

# Audit a high-risk keyword's definition/reference pair
spec-integrator llm-keyword-review --keyword SCHED_DISPATCH_TIMEOUT

# Search stored findings without an API call
spec-integrator llm-findings --min-confidence 0.70
spec-integrator llm-findings --backend clef-flash --min-confidence 0.70
```
Document-review checklists classify each criterion and store backend-specific scores. Jev returns API confidence. Clef Flash returns probability concentration, which is not the probability that a verdict is correct. By default, `llm-findings` searches the current backend; use `--backend` to query another backend. Findings are classified as clear violations, possible violations, known unresolved issues, insufficient context, recommendations, or no issue. Clear violations produce `FAIL` or `WARN` according to configured severity. Possible violations and insufficient context produce `WARN`; unresolved issues and recommendations produce `INFO`.

Keyword audits send each definition section and each traceability reference section as a separate `DEFINITION` or `REFERENCE` item to the selected backend. Re-read the cited sections when evaluating `WARN` or `INFO` findings.

The root `spec-integrator.yaml` is authoritative for audit IDs, target modes, and severity. Individual audit instructions live in `prompts/checks/`. Risk, terminology, and document reviews are sent as checklists with evidence and typed questions. `--dry-run` prints the checklist that would be sent.

Per-criterion classifications and scores are stored in the SQLite `judge_evaluations` table. `llm-findings --min-confidence 0.70` searches `confirmed_violation` and `possible_violation` findings for the default backend. Use `--backend` to select a score type, `--all-outcomes` to include other outcomes, and `--run-type` to filter by audit command.

---

## Configuration Example (`spec-integrator.yaml`)

```yaml
version: "1.0"

project:
  name: "Fireball Hypervisor"
  docs_root: "docs"
  cache_db: ".spec-integrator/doc_cache.db"
  exclude_patterns:
    - "**/FORMAT.md"
    - "FORMAT.md"

tiers:
  - tier: 0
    name: "Requirements"
    path_pattern: 'requires/.*\\.md'
    description: "Top-level system requirements (Why)"

  - tier: 1
    name: "Primary Components"
    path_pattern: 'components/tier1_.*\\.md'
    description: "Coarse-grained primary system components (What)"

  - tier: 2
    name: "Decomposed Subcomponents"
    path_pattern: 'components/tier2_.*\\.md'
    description: "Decomposed subsystems (How - Subsystem)"

  - tier: 3
    name: "Leaf & Platform Components"
    path_pattern: 'components/tier3_.*\\.md'
    description: "Detailed leaf and platform components (How - Leaf)"

  - tier: "meta"
    name: "Architecture & Plans"
    path_pattern: '(architecture|plans)/.*\\.md'
    description: "Cross-tier architecture and development plans"

keywords:
  meta:
    pattern: "^META_[A-Za-z0-9_]+$"
    defined_in: 'architecture/document_structure\\.md'
  global:
    pattern: "^GLOBAL_[A-Za-z0-9_]+$"
    defined_in: 'architecture/document_structure\\.md'
  local:
    pattern: "^[A-Za-z0-9_]+$"
    defined_in: 'requires/.*\\.md'

formal_verification:
  model_dir_name: "formal"
  tag: "{VERIFY_FORMAL}"
  timeout_seconds: 30

wit_verification:
  wit_dir_name: "wit"
  tag: "{VERIFY_WIT}"

llm_judge:
  tag: "{VERIFY_LLM}"
  default_backend: "clef-flash"
  error_score_threshold: 0.70
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
```

`llm_judge.error_score_threshold` is the minimum backend score for a criterion configured with `ERROR` severity to fail the gate. A `confirmed_violation` below this threshold is downgraded to `WARNING`, so a low-confidence or low-concentration judgment alone does not fail the quality gate. The value must be between `0.0` and `1.0`.

---

## Detailed Specification

See [docs/specification.md](docs/specification.md) for the architecture, DocGraph model, database schema, and API/CLI reference.

---

## License

Simplified BSD License — see [LICENSE](LICENSE).
