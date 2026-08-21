# spec-integrator

`spec-integrator` は、仕様駆動開発（Specification-Driven Development）を採用するソフトウェア・システム開発プロジェクトにおいて、ドキュメントの構造、要求トレーサビリティ、Tier 階層依存、形式検証モデル（pyModelChecking）、およびセマンティック整合性（LLM as a Judge）を CI 上で厳格に自動検証・可視化するための汎用 Python CLI ツールです。

---

## 主な機能

- **設定駆動型（Configuration-Driven）**: `spec-integrator.yaml` により、プロジェクト固有のドキュメント階層（Tier 0〜3）やキーワード定義元を明示的に宣言。
- **トポロジカル・ドキュメント空間（DocGraph）**: ファイル・見出しセクション・要求キーワードを有向グラフ（DAG）としてモデル化し、トレーサビリティや局所サブグラフ（$G_r$）を抽出。
- **7段階の厳格な品質ゲート (`check` コマンド)**:
  1. **Format Gate**: Markdown 相対リンクおよび見出しアンカーの存在検証
  2. **Traceability Gate**: 未定義キーワード参照・未参照要件の検証
  3. **Hierarchy Gate**: 上位 Tier から下位 Tier への逆流依存（カプセル化違反）の防止
  4. **Formal Gate**: `{VERIFY_FORMAL}` に連動して pyModelChecking モデルを実行し、さらに**モデル自体の妥当性**（空虚な命題・到達不能状態・単一経路モデル）を監査
  5. **WIT Gate**: `{VERIFY_WIT}` に連動した WIT インターフェイス定義の検証
  6. **Evidence Gate**: 「検証済み」「証明完了」等の主張が実際の成果物に裏付けられているかの検証
  7. **Obligation Gate**: リスク評価が要求した検証が実施されずに放置されていないかの検証

### 検証のサボりを検出する (Anti-Sabotage)

品質ゲートは「実施した検証が失敗したこと」だけでなく、
**「実施すべき検証を実施しなかったこと」**を ERROR として扱う。

| ルール | 検出する状況 |
| :--- | :--- |
| `FORMAL-MODEL-NO-CONTRACT` | モデルが `build_model()` / `properties()` を公開せず、監査できない |
| `FORMAL-PROPERTY-VACUOUS` | 違反状態が状態空間に存在せず、命題が構造上必ず真になっている |
| `FORMAL-MODEL-UNSOUND` | 到達不能状態がある／単一経路でインターリーブを表現できない |
| `FORMAL-BACKING-AMBIGUOUS` | 1つのモデルが複数の無関係な設計書の根拠として二重計上されている |
| `EVID-UNBACKED-CLAIM` | 「検証済み」と書かれているが対応する検証が存在しない・合格していない |
| `EVID-DANGLING-ARTIFACT-REF` | 存在しないモデル・レポート・設計書を参照している |
| `EVID-UNSOURCED-MEASUREMENT` | 「測定環境」「実測値」と書かれているが測定成果物が無い |
| `OBLIG-ASSESSMENT-MISSING` | `assess` を一度も実行せずに `check` を合格させようとしている |
| `OBLIG-ASSESSMENT-STALE` | 文書がリスク評価後に変更され、評価が現状を反映していない |
| `OBLIG-VERIFICATION-SKIPPED` | リスク 4/5 以上と評価されたセクションに要求された検証タグが無い |
| `OBLIG-JUDGE-MISSING` / `-SKIPPED` | `{VERIFY_LLM}` を宣言しながら意味監査が実行されていない／対象に含まれていない |

形式検証モデルが満たすべき契約は **[docs/formal_model_contract.md](docs/formal_model_contract.md)** を参照。
- **LLM as a Judge セマンティック監査 (`judge` コマンド)**:
  - `{VERIFY_LLM}` 指定サブグラフの仕様矛盾・記述漏れを Sakura / Ollama バックエンドで診断。
- **SQLite データベース・監査キャッシュ (`DocAuditDB`)**:
  - ドキュメント構造の高速クエリと、ハッシュ値による差分検証キャッシュ。
- **CI / GitHub Actions ファースト**:
  - サマリー表、違反詳細、トレーサビリティマトリクス、Mermaid 依存図を集約した単一 Markdown レポートおよびマシン可読な `graph.json` を出力。

---

## クイックスタート

### 1. インストール (uv)
```bash
cd tools/spec-integrator  # またはプロジェクトルート
uv pip install -e .
```

### 2. 設定ファイルの初期化
```bash
spec-integrator init
```
カレントディレクトリに `spec-integrator.yaml` の雛形が生成されます。

### 3. ドキュメント検証の実行 (CI 標準)
```bash
spec-integrator check --config spec-integrator.yaml --report report.md
```
全ゲート（Format / Traceability / Hierarchy / Formal）がパスすれば終了コード `0`、エラーがあれば `1` となり、`report.md` に詳細レポートが出力されます。

### 4. DocGraph の可視化
```bash
# Mermaid 形式で標準出力
spec-integrator graph

# JSON 形式でファイル出力
spec-integrator graph -f json -o graph.json
```

### 5. LLM as a Judge 監査の実行
```bash
spec-integrator judge --backend sakura
```

---

## 設定ファイル例 (`spec-integrator.yaml`)

```yaml
version: "1.0"

project:
  name: "Fireball Hypervisor"
  docs_root: "docs"
  cache_db: ".spec-integrator/doc_cache.db"

tiers:
  - tier: 0
    name: "Requirements"
    path_pattern: "docs/requires/**/*.md"
    description: "システム要求仕様書（正本）"

  - tier: 1
    name: "Core & Interface"
    path_pattern: "docs/components/tier1_*/**/*.md"
    description: "システムコア・共通インターフェース仕様書"

  - tier: 2
    name: "Runtime & Engine"
    path_pattern: "docs/components/tier2_*/**/*.md"
    description: "実行エンジン・JIT仕様書"

  - tier: 3
    name: "Platform & HAL"
    path_pattern: "docs/components/tier3_*/**/*.md"
    description: "ハードウェア・プラットフォーム抽象化仕様書"

  - tier: "meta"
    name: "Architecture & Plans"
    path_pattern: "docs/{architecture,plans}/**/*.md"
    description: "全体アーキテクチャ・開発計画"

keywords:
  meta:
    pattern: "^META_[A-Za-z0-9_]+$"
    defined_in: "docs/architecture/document_structure.md"
  global:
    pattern: "^GLOBAL_[A-Za-z0-9_]+$"
    defined_in: "docs/architecture/document_structure.md"
  local:
    pattern: "^[A-Za-z0-9_]+$"
    defined_in: "docs/requires/**/*.md"

formal_verification:
  model_dir_name: "formal"
  tag: "{VERIFY_FORMAL}"
  timeout_seconds: 30

llm_judge:
  tag: "{VERIFY_LLM}"
  default_backend: "sakura"
  backends:
    sakura:
      api_key_env: "SAKURA_API_KEY"
      model: "sakura-ai-model"
    ollama:
      endpoint: "http://localhost:11434"
      model: "llama3"
```

---

## 詳細仕様書
詳細なアーキテクチャ設計、DocGraph 空間モデル、データベーススキーマ、API・CLI リファレンスは [docs/specification.md](docs/specification.md) を参照してください。

---

## ライセンス
Simplified BSD License - 詳細は [LICENSE](LICENSE) を参照してください。
