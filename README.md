# spec-integrator

`spec-integrator` は、仕様駆動開発（Specification-Driven Development）を採用するソフトウェア・システム開発プロジェクトにおいて、ドキュメントの構造、要求トレーサビリティ、Tier 階層依存、形式検証モデル（pyModelChecking / 変異検査）、Mermaid ダイアグラム（組み込み JS エンジン `mermaidx`）、WIT インターフェイス、およびセマンティック整合性（LLM as a Judge / リスク評価）を CI 上で厳格に自動検証・可視化するための汎用 Python CLI ツールです。

---

## 主な機能

- **設定駆動型（Configuration-Driven）**: `spec-integrator.yaml` により、プロジェクト固有のドキュメント階層（Tier 0〜3）やキーワード定義元を明示的に宣言。
- **トポロジカル・ドキュメント空間（DocGraph）**: ファイル・見出しセクション・要求キーワードを有向グラフ（DAG）としてモデル化し、トレーサビリティや局所サブグラフ（$G_r$）を抽出。
- **8段階の厳格な品質ゲート (`check` コマンド)**:
  1. **Format Gate**: Markdown 相対リンクおよび見出しアンカーの存在検証、および `mermaidx`（QuickJS 組み込み JS エンジン）による Mermaid ダイアグラム構文・レンダリング検証
  2. **Traceability Gate**: 未定義キーワード参照・未参照要件の検証
  3. **Hierarchy Gate**: 上位 Tier から下位 Tier への逆流依存（カプセル化違反）の防止
  4. **Formal Gate**: `{VERIFY_FORMAL}` に連動して pyModelChecking モデルを実行し、空虚な命題の排除、および**変異検査（Mutation Testing: `guards=False` で違反状態到達可能の実証による省略偽証明の排除）**
  5. **WIT Gate**: `{VERIFY_WIT}` に連動した WIT (WebAssembly Interface Types) インターフェイス定義の構文・整合性検証
  6. **Evidence Gate**: 「検証済み」「証明完了」「実測値」等の主張が実際の成果物に裏付けられているかの検証
  7. **Obligation Gate**: リスク評価（`assess`）が要求した検証義務が実施されずに放置されていないかの検証
  8. **Consistency Gate**: 修正漏れ（値のドリフト・定義変更が参照側へ未伝播）の検知（`spec-consistency.lock` 連動）

### 検証のサボりを検出する (Anti-Sabotage)

品質ゲートは「実施した検証が失敗したこと」だけでなく、
**「実施すべき検証を実施しなかったこと」**を ERROR として扱う。

| ルール | 検出する状況 |
| :--- | :--- |
| `FMT-BROKEN-LINK` / `-ANCHOR` | 存在しないファイルへのリンクや見出しアンカー |
| `FMT-INVALID-MERMAID` | `mermaidx` (QuickJS) による Mermaid ダイアグラムの構文・レンダリングエラー |
| `FORMAL-MODEL-NO-CONTRACT` | モデルが `build_model()` / `properties()` を公開せず、監査できない |
| `FORMAL-PROPERTY-VACUOUS` | 違反状態が状態空間に存在せず、命題が構造上必ず真になっている |
| `FORMAL-MODEL-UNSOUND` | 到達不能状態がある／単一経路でインターリーブを表現できない |
| `FORMAL-PROPERTY-INVALID` | 変異検査（`guards=False`）で違反状態に到達せず、保護機構が機能していない（省略による偽証明） |
| `FORMAL-BACKING-AMBIGUOUS` | 1つのモデルが複数の無関係な設計書の根拠として二重計上されている |
| `EVID-UNBACKED-CLAIM` | 「検証済み」と書かれているが対応する検証が存在しない・合格していない |
| `EVID-DANGLING-ARTIFACT-REF` | 存在しないモデル・レポート・設計書を参照している |
| `EVID-UNSOURCED-MEASUREMENT` | 「測定環境」「実測値」と書かれているが測定成果物が無い |
| `OBLIG-ASSESSMENT-MISSING` | `assess` を一度も実行せずに `check` を合格させようとしている |
| `OBLIG-ASSESSMENT-STALE` | 文書がリスク評価後に変更され、評価が現状を反映していない |
| `OBLIG-VERIFICATION-SKIPPED` | リスク 4/5 以上と評価されたセクションに要求された検証タグが無い |
| `OBLIG-JUDGE-MISSING` / `-SKIPPED` | `{VERIFY_LLM}` を宣言しながら意味監査が実行されていない／対象に含まれていない |
| `CONSIST-SYMBOL-DRIFT` | 同一シンボルが場所によって違う値を持つ（設定不要。表記ゆれは正規化して比較） |
| `CONSIST-COCHANGE-STALE` | キーワードの定義を変更したのに、参照側の節が旧記述のまま |
| `CONSIST-STALE-VALUE` | 移行済みの旧値がどこかに残存している |

形式検証モデルが満たすべき契約は **[docs/formal_model_contract.md](docs/formal_model_contract.md)** を参照。

### 修正漏れの検知 (`sync` / lockfile)

`check` は `spec-consistency.lock` を基準に「伝播しなかった編集」を検出します。

```bash
spec-integrator check   # 検査実行 → 伝播漏れが列挙される
spec-integrator sync    # 全て伝播・修正したら基準を更新（lockfile はコミットする）
```

`sync` を `check` に組み込んでいないのは意図的です。自動更新すると、漏れを暴くための記録そのものが消えます。
co-change の依存関係は `{Keyword}` の既存トレーサビリティから自動導出されるため、宣言の手書きは不要です。

- **リスク評価・検証義務導出 (`assess` コマンド)**:
  - 各ドキュメントセクションの複雑度・設計リスクを評価し、形式検証・LLM 監査が必要な箇所を `{VERIFY_FORMAL}` / `{VERIFY_LLM}` 義務として記録。
- **LLM as a Judge セマンティック監査 (`judge` コマンド)**:
  - `{VERIFY_LLM}` 指定サブグラフの仕様矛盾・記述漏れを Sakura / Ollama バックエンドで診断。
- **SQLite データベース・監査キャッシュ (`DocAuditDB`)**:
  - ドキュメント構造の高速クエリと、ハッシュ値による差分検証キャッシュ。
- **CI / GitHub Actions ファースト**:
  - 検査器リビジョン刻印（Rule R9 準拠）、サマリー表、違反詳細、トレーサビリティマトリクスを集約した単一 Markdown レポートおよびマシン可読な `graph.json` を出力。

---

## クイックスタート

### 1. インストール (uv)
```bash
cd tools/spec-integrator  # またはプロジェクトルート
uv sync --system-certs
```

### 2. 設定ファイルの初期化
```bash
spec-integrator init
```
カレントディレクトリに `spec-integrator.yaml` の雛形が生成されます。

### 3. ドキュメント検証の実行 (CI 標準)
```bash
# 全 8 ゲートのクリーン検証
spec-integrator check --config spec-integrator.yaml --report report.md --clean
```
全 8 ゲートがパスすれば終了コード `0`、エラーがあれば `1` となり、`report.md` に詳細レポートが出力されます。

### 4. リスク評価と検証義務の導出
```bash
spec-integrator assess --config spec-integrator.yaml --backend sakura --report doc_risk_report.md
```

### 5. 一貫性ベースラインの更新
```bash
spec-integrator sync --config spec-integrator.yaml
```

### 6. DocGraph の可視化
```bash
# Mermaid 形式で標準出力
spec-integrator graph

# JSON 形式でファイル出力
spec-integrator graph -f json -o graph.json
```

### 7. LLM as a Judge 監査の実行
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
  exclude_patterns:
    - "**/FORMAT.md"
    - "FORMAT.md"

tiers:
  - tier: 0
    name: "Requirements"
    path_pattern: 'requires/.*\.md'
    description: "システム最上位要求仕様書（Why）"

  - tier: 1
    name: "Primary Components"
    path_pattern: 'components/tier1_.*\.md'
    description: "粗粒度主要システムコンポーネント（What）"

  - tier: 2
    name: "Decomposed Subcomponents"
    path_pattern: 'components/tier2_.*\.md'
    description: "分解されたサブコンポーネント群（How - Subsystem）"

  - tier: 3
    name: "Leaf & Platform Components"
    path_pattern: 'components/tier3_.*\.md'
    description: "詳細リーフおよびプラットフォームコンポーネント（How - Leaf）"

  - tier: "meta"
    name: "Architecture & Plans"
    path_pattern: '(architecture|plans)/.*\.md'
    description: "全Tier横断メタ設計・開発計画"

keywords:
  meta:
    pattern: "^META_[A-Za-z0-9_]+$"
    defined_in: 'architecture/document_structure\.md'
  global:
    pattern: "^GLOBAL_[A-Za-z0-9_]+$"
    defined_in: 'architecture/document_structure\.md'
  local:
    pattern: "^[A-Za-z0-9_]+$"
    defined_in: 'requires/.*\.md'

formal_verification:
  model_dir_name: "formal"
  tag: "{VERIFY_FORMAL}"
  timeout_seconds: 30

wit_verification:
  wit_dir_name: "wit"
  tag: "{VERIFY_WIT}"

llm_judge:
  tag: "{VERIFY_LLM}"
  default_backend: "sakura"
  backends:
    sakura:
      api_key_env: "SAKURA_API_KEY"
      model: "preview/gemma-4-31B-it"
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
