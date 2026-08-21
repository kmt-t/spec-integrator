# 形式検証モデルの契約 (Formal Model Contract)

`{VERIFY_FORMAL}` を宣言した設計書の `formal/*.py` は、**監査可能な契約**を満たさなければならない。

この契約の目的はひとつである。

> **反証できないモデルは証明ではない。**

`print("PASS")` するだけのスクリプトや、違反状態を最初から状態集合に入れていないモデルは、
検査器を必ず通過する。それは設計の正しさではなく、モデルの書き方を反映しているにすぎない。
Formal Gate はこれを機械的に拒否する。

---

## 1. 必須 API

| シンボル | 種別 | 必須 | 役割 |
| :--- | :--- | :---: | :--- |
| `build_model()` | 関数 | ✅ | `pyModelChecking.Kripke` を返す。監査器が状態空間を直接検査する。 |
| `properties()` | 関数 | ✅ | 検査するプロパティ記述子のリストを返す。 |
| `BACKS` | リスト | △ | このモデルが証明を引き受ける設計書のパス。同一 `formal/` を複数文書が共有する場合は必須。 |
| `verify()` | 関数 | ✕ | 旧形式。レポートの補助テキストとしてのみ使われ、合否判定には用いられない。 |

### プロパティ記述子

```python
{
    "name":      "mutual_exclusion",   # レポート上の識別名
    "kind":      "safety",             # safety | liveness | reachability | deadlock_freedom
    "logic":     "CTL",                # CTL | LTL（省略時は式のモジュールから推定）
    "formula":   AG(Not(bad)),         # 検査する式
    "violation": bad,                  # ★ この式を満たす状態が存在しなければ NG
    "expect":    True,                 # 期待する結果（False = 意図的に反証されることを示す）
}
```

`violation` は `AG(Not(φ))` の形からは自動導出されるため省略できるが、
`kind` が `safety` / `deadlock_freedom` で導出もできない場合は **ERROR** になる。

---

## 2. 監査される内容

| ルール | 内容 |
| :--- | :--- |
| `FORMAL-MODEL-NO-CONTRACT` | `build_model()` / `properties()` が無く、状態空間を検査できない。 |
| `FORMAL-PROPERTY-VACUOUS` | `violation` を満たす状態が状態空間に**存在しない**。プロパティは設計ではなくラベル付けの都合で真になっている。 |
| `FORMAL-PROPERTY-INVALID` | 式中の原子命題がどの状態ラベルにも現れない／`liveness` を宣言しながら `AF`・`EF`・`U` 等の到達性演算子を含まない。 |
| `FORMAL-MODEL-UNSOUND` | 初期状態から到達不能な状態がある／状態数が `min_states` 未満／全到達状態の後続が1つ以下（単一経路モデル）。 |
| `FORMAL-BACKING-AMBIGUOUS` | 複数の設計書が同じ `formal/` を根拠にしているのに、どのモデルがどの主張を引き受けるか宣言されていない。 |

### なぜ単一経路モデルが拒否されるか

状態が一本の環を成すだけのモデルには**インターリーブが存在しない**。
デッドロック・競合・スターベーションはいずれも複数の実行順序が絡んで初めて生じるため、
そのようなモデル上で証明された「デッドロック不在」は、モデルの形から自明に従うだけであり、
設計について何も述べていない。

---

## 3. 参照実装

以下は「相互排除を**満たさない**素朴なモデル」を正直に記述した例である。
違反状態 `s_both_crit` が状態空間に存在し、初期状態から到達可能であるため、
検査結果には意味がある。

```python
from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, EF, Not, And, AtomicProposition

BACKS = ["components/tier1_core/os_scheduler.md"]


def build_model() -> Kripke:
    S = ["s_idle", "s_a_crit", "s_b_crit", "s_both_crit", "s_wait"]
    R = [
        ("s_idle", "s_a_crit"), ("s_idle", "s_b_crit"), ("s_idle", "s_wait"),
        ("s_a_crit", "s_both_crit"), ("s_a_crit", "s_idle"),
        ("s_b_crit", "s_idle"), ("s_b_crit", "s_wait"),
        ("s_both_crit", "s_idle"),
        ("s_wait", "s_a_crit"), ("s_wait", "s_idle"),
    ]
    L = {
        "s_idle":      {"idle"},
        "s_a_crit":    {"a_crit"},
        "s_b_crit":    {"b_crit"},
        "s_both_crit": {"a_crit", "b_crit"},   # ← 違反状態を必ず表現する
        "s_wait":      {"waiting"},
    }
    return Kripke(S=S, S0={"s_idle"}, R=R, L=L)


def properties():
    bad = And(AtomicProposition("a_crit"), AtomicProposition("b_crit"))
    return [
        {
            "name": "mutual_exclusion",
            "kind": "safety",
            "logic": "CTL",
            "formula": AG(Not(bad)),
            "violation": bad,
            "expect": False,   # このモデルは相互排除を保証しない、と明示する
        },
    ]
```

保護機構（ロック変数・ターン変数など）を状態に加えれば `expect: True` に変わる。
**`expect` を True にするために違反状態を消してはならない。** それが `FORMAL-PROPERTY-VACUOUS` である。

---

## 4. 注意: Kripke 構造は全域的

`pyModelChecking.Kripke` は遷移関係 `R` が全域であることを要求する（後続を持たない状態を作れない）。
したがって**デッドロックはシンク状態では表現できない**。
`deadlock` ラベルを持つ状態に自己ループを張り、`violation` にその命題を指定すること。

```python
("s_deadlock", "s_deadlock"),
...
"s_deadlock": {"deadlock"},
...
{
    "name": "deadlock_freedom",
    "kind": "deadlock_freedom",
    "formula": AG(EF(AtomicProposition("progress"))),
    "violation": AtomicProposition("deadlock"),
    "expect": True,
}
```
