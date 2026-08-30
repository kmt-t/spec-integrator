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
    "name": "mutual_exclusion",  # レポート上の識別名
    "kind": "safety",  # safety | liveness | reachability | deadlock_freedom
    "logic": "CTL",  # CTL | LTL（省略時は式のモジュールから推定）
    "formula": AG(Not(bad)),  # 検査する式
    "violation": bad,  # ★ この式を満たす状態が存在しなければ NG
    "expect": True,  # 期待する結果（False = 意図的に反証されることを示す）
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
| `FORMAL-PROPERTY-INVALID` | 式中の原子命題がどの状態ラベルにも現れない／`liveness` を宣言しながら到達性演算子を含まない、**または `EF`・`EU`（存在量化）で進行を主張している**。 |
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
from pyModelChecking.CTL import AG, Not, And, AtomicProposition

BACKS = ["components/tier1_core/os_scheduler.md"]


def build_model() -> Kripke:
    S = ["s_idle", "s_a_crit", "s_b_crit", "s_both_crit", "s_wait"]
    R = [
        ("s_idle", "s_a_crit"),
        ("s_idle", "s_b_crit"),
        ("s_idle", "s_wait"),
        ("s_a_crit", "s_both_crit"),
        ("s_a_crit", "s_idle"),
        ("s_b_crit", "s_idle"),
        ("s_b_crit", "s_wait"),
        ("s_both_crit", "s_idle"),
        ("s_wait", "s_a_crit"),
        ("s_wait", "s_idle"),
    ]
    L = {
        "s_idle": {"idle"},
        "s_a_crit": {"a_crit"},
        "s_b_crit": {"b_crit"},
        "s_both_crit": {"a_crit", "b_crit"},  # ← 違反状態を必ず表現する
        "s_wait": {"waiting"},
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
            "expect": False,  # このモデルは相互排除を保証しない、と明示する
        },
    ]
```

### ⚠ これは出発点であって、到達点ではない

上の例は **`expect: False`**、すなわち「このモデルは相互排除を保証しない」と述べているだけである。
反証可能性は確保されたが、**設計が正しいことは何も証明していない。**

到達すべき形は、**違反に至る経路をモデルに書いたうえで、保護機構がそれを断つこと**である。

### ⚠ 「入る辺を描かない」は証明ではない

違反状態に入る辺を単に書かなければ、`AG(Not(bad))` は当然に成り立つ。
しかしそれは**設計が違反を防いでいる**のではなく、**モデル作成者が遷移を書き忘れた**のと
構造上まったく区別がつかない。式も、状態集合も、検査結果も同一になる。

したがって、**保護機構は「切れるもの」としてモデルに現れなければならない。**

```python
def build_model(*, guards: bool = True) -> Kripke:
    S = ["s_idle", "s_a_crit", "s_b_crit", "s_both_crit", "s_wait"]
    R = [
        ("s_idle", "s_a_crit"),
        ("s_idle", "s_b_crit"),
        ("s_idle", "s_wait"),
        ("s_a_crit", "s_idle"),
        ("s_b_crit", "s_idle"),
        ("s_wait", "s_b_crit"),
        ("s_wait", "s_idle"),
        ("s_both_crit", "s_idle"),
    ]
    if not guards:
        # ロックが無ければ、A が臨界区間にいる間に B も入れてしまう。
        # ここに「違反が起きる経路」を書く。ガード有効時はこの辺が存在しない。
        R = R + [("s_a_crit", "s_both_crit"), ("s_b_crit", "s_both_crit")]
    ...


{
    "name": "mutual_exclusion",
    "kind": "safety",
    "formula": AG(Not(bad)),
    "violation": bad,
    "expect": True,
}
```

ゲートは `build_model(guards=False)` を自動的に構築し、
**ガードを外すと違反状態が到達可能になること**を確認する。
到達可能にならなければ、そのガードは何も防いでいない（`FORMAL-PROPERTY-INVALID`）。

これは**変異検査**である。「守っている」と主張するなら、守りを外したときに壊れなければならない。

| 状態 | `violation` を満たす状態が… | `expect` | 意味 |
| :--- | :--- | :---: | :--- |
| 空虚 | 状態集合に**存在しない** | True | ❌ ラベル付けの都合で真。証明ではない |
| 反証デモ | **到達可能** | False | ⚠ 検査器が動くことの実証。設計は未証明 |
| **省略による偽証明** | 存在するが**入る辺が無い** | True | ❌ 遷移を書き忘れたのと区別がつかない |
| **証明** | ガード無効時に**到達可能**になる | True | ✅ 保護機構が違反を防いでいることの証明 |

**`expect: False` のモデルしか無い状態で、設計書がその性質を「満たす」と書いてはならない。**
モデルは反対のことを述べている。この矛盾はゲートでは検出できないため、
`expect: False` には `refutation_note` の記載を必須とし、
それが根拠として紐づく設計書（`BACKS`）に対して警告を出す。

---

## 4. 注意: Kripke 構造は全域的

`pyModelChecking.Kripke` は遷移関係 `R` が全域であることを要求する（後続を持たない状態を作れない）。
したがって**デッドロックはシンク状態では表現できない**。
`deadlock` ラベルを持つ状態に自己ループを張り、`violation` にその命題を指定すること。

```python
from pyModelChecking.CTL import AG, AF, Imply, AtomicProposition

("s_deadlock", "s_deadlock"),
...
"s_deadlock": {"deadlock"},
...
{
    "name": "deadlock_freedom",
    "kind": "deadlock_freedom",
    # AF: どの実行経路をたどっても必ず progress に到達する
    "formula": AG(Imply(AtomicProposition("requested"), AF(AtomicProposition("progress")))),
    "violation": AtomicProposition("deadlock"),
    "expect": True,
}
```

## 5. 進行の主張には全称量化子を使うこと

`liveness` / `deadlock_freedom` / `response` を宣言したプロパティで
**`EF`・`EU`（存在量化）を使うことは禁止**であり、ゲートは ERROR とする。

| 式 | 意味 | 進行の証明になるか |
| :--- | :--- | :---: |
| `AG(p -> EF q)` | p のとき q に到達**し得る** | ❌ |
| `AG(p -> AF q)` | p のとき q に**必ず到達する** | ✅ |

`AG(p -> EF q)` は、その分岐を永久に選ばずループし続ける実行を許す。
分岐が1つでも存在すれば真になるため、強連結なモデルではほぼ自明に成立する。
**「到達可能である」は「進行する」ではない。**
