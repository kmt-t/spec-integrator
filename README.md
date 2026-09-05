# spec-integrator

`spec-integrator` は、仕様駆動開発（Specification-Driven Development）を採用するソフトウェア・システム開発プロジェクトにおいて、ドキュメントの構造、要求トレーサビリティ、Tier 階層依存、形式検証モデル（pyModelChecking / 変異検査）、Mermaid ダイアグラム（組み込み JS エンジン `mermaidx`）、WIT インターフェイス、およびセマンティック整合性（LLM as a Judge / リスク評価）を CI 上で厳格に自動検証・可視化するための汎用 Python CLI ツールです。

---

## 主な機能

- **設定駆動型（Configuration-Driven）**: `spec-integrator.yaml` により、プロジェクト固有のドキュメント階層（Tier 0〜3）やキーワード定義元を明示的に宣言。
- **トポロジカル・ドキュメント空間（DocGraph）**: ファイル・見出しセクション・要求キーワードを有向グラフ（DAG）としてモデル化し、トレーサビリティや局所サブグラフ（$G_r$）を抽出。
- **8段階の厳格な品質ゲート (`check-doc` コマンド)**:
  1. **Format Gate**: Markdown 相対リンクおよび見出しアンカーの存在検証、ファイルリンク形式（ベースネーム表記・プロジェクトルート相対パス強制）、レーベンシュタイン距離による静的タイポ、および `mermaidx`（QuickJS 組み込み JS エンジン）による Mermaid ダイアグラム構文・レンダリング検証
  2. **Traceability Gate**: 未定義キーワード参照・未参照要件の検証
  3. **Hierarchy Gate**: 上位 Tier から下位 Tier への逆流依存（カプセル化違反）の防止
  4. **Formal Gate**: `{VERIFY_FORMAL}` に連動して pyModelChecking モデルを実行し、空虚な命題の排除、および**変異検査（Mutation Testing: `guards=False` で違反状態到達可能の実証による省略偽証明の排除）**
  5. **WIT Gate**: `{VERIFY_WIT}` に連動した WIT (WebAssembly Interface Types) インターフェイス定義の構文・整合性検証
  6. **Evidence Gate**: 「検証済み」「証明完了」「実測値」等の主張が実際の成果物に裏付けられているかの検証
  7. **Obligation Gate**: リスク評価（`risk`）が要求した検証義務が実施されずに放置されていないかの検証
  8. **Consistency Gate**: 修正漏れ（値のドリフト・定義変更が参照側へ未伝播）の検知（一貫性ベースライン連動）

### 検証のサボりを検出する (Anti-Sabotage)

品質ゲートは「実施した検証が失敗したこと」だけでなく、
**「実施すべき検証を実施しなかったこと」**を ERROR として扱う。

以下は全て、ファイルの存在・ハッシュ一致・タグの有無・状態空間の構造など、
**実行結果だけで機械的に真偽が決まる**確認項目である。LLM の意味判断
（`llm-judge` の PASS/FAIL 自体）に依存する項目はここに含めない —— その正否は
アルゴリズムで形式的に確認できないため、Anti-Sabotage の対象から意図的に外している。

確認項目名は全行「〔対象〕の〔問題種別〕」の形に正規化し、問題種別は
`欠落 / 陳腐化 / 不一致 / 破綻 / 空虚化 / 不備 / 曖昧化 / エラー / 未実施 /
自己参照 / 不足 / 重複 / 残存 / 未伝播 / 未固定 / 漏れ` という共通語彙に統一する。

| ゲート | 確認項目（正規化名） | ルールコード | 判定基準（何を機械的に確認するか） |
| :--- | :--- | :--- | :--- |
| Format | リンク先の欠落 | `FMT-BROKEN-LINK` | Markdown の相対リンクが指すファイルが実際に存在するかをファイルシステムで確認する。 |
| Format | 見出しアンカーの欠落 | `FMT-BROKEN-ANCHOR` | リンク先の見出しアンカーが対象ドキュメント内に実在するかを確認する。 |
| Format | ファイルリンク形式の不備 | `FMT-FILE-LINK-FORMAT` | ファイルリンクの表示テキストがファイル名（basename）であり、リンク先がプロジェクトルート相対パス形式（`../` や絶対パス禁止）であるか、未リンクの生ファイルパスが残存していないかを確認する。 |
| Format | 静的タイポ・表記揺れの残存 | `FMT-LEVENSHTEIN-TYPO` | 定義済みキーワードや見出しに対するレーベンシュタイン距離に基づく類似度チェックにより、静的な打ち間違いや誤字・脱字を検出する。 |
| Format | Mermaid 構文のエラー | `FMT-INVALID-MERMAID` | `mermaidx`（組み込み QuickJS）で実際にダイアグラムをパース・レンダリングし、失敗したら ERROR とする（目視ではなく実行結果で判定）。 |
| Format | Mermaid 検証エンジンの欠落 | `FMT-MERMAID-VALIDATOR-UNAVAILABLE` | `mermaidx` が import できない場合、検証を素通りさせず ERROR にする（ツール不備による無検査状態の禁止）。 |
| Formal | 形式モデルの欠落 | `FORMAL-MODEL-NOT-FOUND` | `{VERIFY_FORMAL}` を宣言しているのに、対応する `formal/*.py` モデルが存在せず `BACKS` でも紐付いていない。 |
| Formal | 監査契約の欠落 | `FORMAL-MODEL-NO-CONTRACT` | モデルが `build_model()` / `properties()` を公開しておらず、状態空間を機械的に検査できない。 |
| Formal | モデル構造の破綻 | `FORMAL-MODEL-UNSOUND` | 初期状態から到達不能な状態が残っている、または全状態の分岐数が 1 以下（単一経路でインターリーブを表現できない）などの構造欠陥を検出する。 |
| Formal | 検証命題の空虚化 | `FORMAL-PROPERTY-VACUOUS` | 「違反状態」を満たす状態がモデル中に 1 つも無く、命題が構造上必ず真になっているだけで、設計が守っているとは言えない。 |
| Formal | 性質記述の不備 | `FORMAL-PROPERTY-INVALID` | 原子命題が状態ラベルに一度も出現しない、または `guards=False`（変異検査）でも違反状態に到達できず保護機構の実効性を確認できないなど、記述自体が検査として成立していない。 |
| Formal | 裏付けモデルの曖昧化 | `FORMAL-BACKING-AMBIGUOUS` | 同じ `formal/` ディレクトリを複数の設計書が根拠にしているのに、どのモデルがどの文書を裏付けるかを `BACKS` で明示していない。 |
| Evidence | 証跡ファイルの欠落 | `EVID-DECLARED-FILE-MISSING` | `<!-- evidence: ... -->` に書かれたパスがファイルシステム上に実在するかを確認する。 |
| Evidence | 形式検証証跡の不一致 | `EVID-FORMAL-UNDECLARED` | `{VERIFY_FORMAL}` を宣言しているのに `evidence:` ブロックに `formal:` エントリが無い。 |
| Evidence | WIT 証跡の不一致 | `EVID-WIT-UNDECLARED` | `{VERIFY_WIT}` を宣言しているのに `evidence:` ブロックに `wit:` エントリが無い。 |
| Evidence | ベンチマーク証跡の不一致 | `EVID-BENCHMARK-UNDECLARED` | `{VERIFY_BENCHMARK}` を宣言しているのに `evidence:` ブロックに `benchmark:` エントリが無い。 |
| Evidence | ベンチマーク実装の欠落 | `EVID-BENCHMARK-MISSING` | `{VERIFY_BENCHMARK}` を宣言しているのに、対応する `benchmarks/*.py` が 1 本も存在しない（実測を主張するなら実行可能な計測コードが要る）。 |
| Evidence | 参照アーティファクトの欠落 | `EVID-DANGLING-ARTIFACT-REF` | 本文中で言及されているファイルパス（モデル・レポート・設計書）が実在するかを確認する。 |
| Obligation | リスク評価の未実施 | `OBLIG-ASSESSMENT-MISSING` | `llm-assess` を一度も実行しておらず、キャッシュ DB の `risk_assessments` テーブルが空である。 |
| Obligation | 評価エンジン記録の欠落 | `OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN` | `run_metadata` テーブルに `backend` が記録されておらず、評価が文書から独立した判断かを確認できない。 |
| Obligation | 評価エンジンの自己参照 | `OBLIG-ASSESSMENT-NOT-INDEPENDENT` | 評価が「文書自身のタグから義務を機械的に逆算するだけの backend（禁止リスト登録済み）」で行われており、達成率が定義上 100% になる自己証明状態を検出する。 |
| Obligation | 評価カバレッジの不足 | `OBLIG-ASSESSMENT-PARTIAL` | 評価済みキーワード数が現在の全キーワード数（`llm-judge` が監査するのと同じ母集団）より少ない（未評価のキーワードが残っている）。 |
| Obligation | リスク評価の陳腐化 | `OBLIG-ASSESSMENT-STALE` | 評価後に文書が変更され、記録されたハッシュと現在の内容が一致しない。 |
| Obligation | 検証タグの欠落 | `OBLIG-VERIFICATION-SKIPPED` | risk_score が閾値以上なのに、対応する `{VERIFY_LLM}` タグが文書に付いていない。 |
| Obligation | 意味監査結果の欠落 | `OBLIG-JUDGE-MISSING` | `{VERIFY_LLM}` を宣言しているのに、キャッシュ DB の `judge_results` テーブルに `llm-judge` の判定結果が存在しない。 |
| Obligation | 意味監査結果の未固定 | `OBLIG-JUDGE-UNANCHORED` | `judge_results` に文書ハッシュが記録されておらず、どの版を監査した結果かを特定できない。 |
| Obligation | 意味監査結果の陳腐化 | `OBLIG-JUDGE-STALE` | `llm-judge` 実行後に文書が変更され、記録されたハッシュと現在の内容が一致しない。 |
| Obligation | 意味監査対象の漏れ | `OBLIG-JUDGE-SKIPPED` | `{VERIFY_LLM}` を宣言しているのに、判定結果の監査対象一覧（covered_files）に含まれていない。 |
| Obligation | 意味監査の不合格 | `OBLIG-JUDGE-FAILED` | この文書が引用するキーワードについて、記録済みの判定結果が FAIL を報告している。 |
| Obligation | 文書単位監査結果の欠落 | `OBLIG-DOC-JUDGE-MISSING` | `{VERIFY_LLM}` を宣言している文書があるのに、キャッシュ DB の `document_judge_results` テーブルが空である。サブグラフ監査でのカバレッジとは独立に判定する。 |
| Obligation | 文書単位監査結果の未固定 | `OBLIG-DOC-JUDGE-UNANCHORED` | `document_judge_results` に文書ハッシュが記録されておらず、どの版を監査した結果かを特定できない。 |
| Obligation | 文書単位監査結果の陳腐化 | `OBLIG-DOC-JUDGE-STALE` | `llm-judge` の文書単位監査後に文書が変更され、記録されたハッシュと現在の内容が一致しない。 |
| Obligation | 文書単位監査対象の漏れ | `OBLIG-DOC-JUDGE-SKIPPED` | `{VERIFY_LLM}` を宣言しているのに、その文書自体が `document_judge_results` に一度も現れていない（サブグラフ経由のカバレッジでは代替できない）。 |
| Obligation | 文書単位監査の不合格 | `OBLIG-DOC-JUDGE-FAILED` | この文書自体について、記録済みの文書単位判定結果が FAIL を報告している。 |
| Consistency | キーワード定義の重複 | `CONSIST-DUPLICATE-DEFINITION` | 同じ `{Keyword}` が要求仕様テーブルの複数行で定義されている。 |
| Consistency | シンボル値の不一致 | `CONSIST-SYMBOL-DRIFT` | 同一シンボル（例: `FB_CONF_*`）がリポジトリ内の複数箇所で異なる値を持つ（設定不要。表記ゆれは正規化して比較）。 |
| Consistency | 連動修正の未伝播 | `CONSIST-COCHANGE-STALE` | キーワード定義側は変更されたが、参照側の記述が一貫性ベースライン（キャッシュ DB 記録値）から更新されていない。 |
| Consistency | 旧値の残存 | `CONSIST-STALE-VALUE` | 設定済みの禁止パターン（移行済みの旧値）が文書中に引き続き残っている。 |

`EVID-UNBACKED-CLAIM` / `EVID-UNSOURCED-MEASUREMENT` は本表から削除した。実装が
存在せず README にのみ記載されていた名称であり、コード上の後継である「検証済み」
等の主張チェックは LLM (`llm-judge`) のプロンプト内だけで行われる —— アルゴリズムでは
正否を確認できないため、Anti-Sabotage の確認項目としては扱わない。

形式検証モデルが満たすべき契約は **[docs/formal_model_contract.md](docs/formal_model_contract.md)** を参照。

### 修正漏れの検知 (`sync`)

`check` は一貫性ベースライン（キャッシュ DB 記録値）を基準に「伝播しなかった編集」を検出します。

```bash
spec-integrator check   # 検査実行 → 伝播漏れが列挙される
spec-integrator sync    # 全て伝播・修正したら基準を更新（キャッシュ DB に記録）
```

`sync` を `check` に組み込んでいないのは意図的です。自動更新すると、漏れを暴くための記録そのものが消えます。
co-change の依存関係は `{Keyword}` の既存トレーサビリティから自動導出されるため、宣言の手書きは不要です。

- **リスク評価・検証義務導出 (`risk` コマンド)**:
  - 各要求／設計キーワードの複雑度・設計リスクをスコアリングし、リスクが閾値以上のキーワードに `{VERIFY_LLM}` 義務を課す（Obligation Gate が読む）。結果はすべて SQLite DB に永続化。
- **用語表記揺れ検査 (`llm-word` コマンド)**:
  - TF-IDF による抽出キーワードとエンベディング類似度・LLM による文脈判定を組み合わせ、用語表記揺れやタイポを高精度に検出。
- **LLM as a Judge セマンティック監査 (`llm-single-review`, `llm-keyword-review` コマンド)**:
  - 単一ドキュメント・セクションの自己一貫性監査、または高リスクキーワードが連結するドキュメント島全体のトレーサビリティ・意味的矛盾を Sakura / OpenRouter / Ollama バックエンドで診断。
- **SQLite データベース・監査キャッシュ (`DocAuditDB`)**:
  - ドキュメント構造の高速クエリ、ハッシュ値による差分検証キャッシュに加え、`risk`/`llm-single-review`/`llm-keyword-review` の判定結果そのもの（中間 JSON レポートは生成しない）を記録する唯一の正本。
  - `.spec-integrator/doc_cache.db` は git 管理対象（`.gitattributes` で `*.db binary` 指定）。フレッシュチェックアウトや CI でも、課金を伴う LLM 監査を再実行せずに直近の監査結果を参照できる。
- **CI / GitHub Actions ファースト**:
  - 検査器リビジョン刻印（Rule R9 準拠）、サマリー表、違反詳細、トレーサビリティマトリクス、リスク評価・LLM 判定結果を集約した単一 Markdown レポートを出力。

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

### 3. ドキュメントDB構築・用語インデックス作成 (`build`)
```bash
spec-integrator build --config spec-integrator.yaml
```

### 4. ドキュメント自動フォーマット & 静的品質ゲート検証 (`format-doc`, `check-doc`)
```bash
# Markdown ドキュメントのフォーマット整形
spec-integrator format-doc --config spec-integrator.yaml

# 全 8 ゲートの静的クリーン検証 (CI 標準)
spec-integrator check-doc --config spec-integrator.yaml --report report.md --clean
```
全 8 ゲートがパスすれば終了コード `0`、エラーがあれば `1` となり、`report.md` に詳細レポートが出力されます。

### 5. ソースコード自動フォーマット & 静的規約・サボり検査 (`format-src`, `check-src`)
```bash
# ソースコード自動整形 (Python: Ruff / C++: clang-format)
spec-integrator format-src --group all

# ソースコード規約・サボり検証 (TODO放置、空関数、typing.Any完全禁止、変異検査等)
spec-integrator check-src --group all
```

### 6. DocGraph の可視化 (`graph`)
```bash
# Mermaid 形式で標準出力
spec-integrator graph

# JSON 形式でファイル出力
spec-integrator graph -f json -o graph.json
```

### 7. リスク評価と検証義務の導出 (`risk`)
```bash
# --backend を省略すると spec-integrator.yaml の llm_judge.default_backend が使われる
# 結果はキャッシュ DB に記録され、check-doc レポートの Risk Assessment Detail 節に反映される
spec-integrator risk --config spec-integrator.yaml
```

### 8. 用語表記揺れチェック (`llm-word`)
```bash
# 静的チェックのみ (高速・0コスト)
spec-integrator llm-word --quick

# エンベディング + LLM 文脈判定込み
spec-integrator llm-word --backend sakura
```

### 9. LLM as a Judge セマンティック監査 (`llm-single-review`, `llm-keyword-review`)
```bash
# 単一ドキュメント／セクション監査
spec-integrator llm-single-review --file docs/components/tier1_core/os_scheduler.md

# 高リスクキーワード連結島監査
spec-integrator llm-keyword-review --keyword SCHED_DISPATCH_TIMEOUT
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
