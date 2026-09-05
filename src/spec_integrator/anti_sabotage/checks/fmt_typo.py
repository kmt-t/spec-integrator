from __future__ import annotations

import re
from collections import defaultdict

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


def levenshtein_distance(s1: str, s2: str) -> int:
    """文字列間のレーベンシュタイン距離を計算する。"""
    if s1 == s2:
        return 0
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


class LevenshteinTypoCheck(AntiSabotageCheck):
    """表記揺れ・誤記の警告: レーベンシュタイン距離を用いて類似単語の揺れを検出する。"""

    rule_code = "FMT-LEVENSHTEIN-TYPO"
    name = "用語の表記揺れ・誤記"
    gate = "Format"
    severity = "WARNING"
    description = "レーベンシュタイン距離を用いたカタカナ・英単語の表記揺れ・タイポの検出。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        stopwords = set(getattr(ctx.config.terminology, "stopwords", None) or [])

        katakana_occs: dict[str, list[tuple[str, int]]] = defaultdict(list)
        english_occs: dict[str, list[tuple[str, int]]] = defaultdict(list)

        katakana_pat = re.compile(r"[\u30A1-\u30FA\u30FC]{3,}")
        english_pat = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{4,}\b")

        for doc in ctx.documents:
            for sec in doc.sections:
                cleaned = re.sub(r"```[\s\S]*?```", " ", sec.body_text)
                cleaned = re.sub(r"`[^`\n]+`", " ", cleaned)
                for m in katakana_pat.finditer(cleaned):
                    w = m.group(0)
                    if w not in stopwords:
                        katakana_occs[w].append((doc.file_path, sec.line_start))
                for m in english_pat.finditer(cleaned):
                    w = m.group(0)
                    lower = w.lower()
                    if lower not in stopwords and not w.isupper():
                        english_occs[w].append((doc.file_path, sec.line_start))

        # Katakana
        reported_pairs: set[tuple[str, str]] = set()
        kata_vocab = sorted(katakana_occs.keys())
        kata_issues: list[VerificationIssue] = []

        for i in range(len(kata_vocab)):
            w1 = kata_vocab[i]
            len1 = len(w1)
            for j in range(i + 1, len(kata_vocab)):
                w2 = kata_vocab[j]
                len2 = len(w2)
                if abs(len1 - len2) > 2:
                    continue

                dist = levenshtein_distance(w1, w2)
                is_typo = (dist == 1) or (
                    dist == 2
                    and len1 >= 5
                    and len2 >= 5
                    and ("ー" in w1 or "ー" in w2 or "イ" in w1 or "イ" in w2)
                )

                if is_typo:
                    pair_key = (min(w1, w2), max(w1, w2))
                    if pair_key in reported_pairs:
                        continue
                    reported_pairs.add(pair_key)

                    occs1 = katakana_occs[w1]
                    occs2 = katakana_occs[w2]
                    target_occ = occs1[0] if len(occs1) <= len(occs2) else occs2[0]
                    other_occ = occs2[0] if len(occs1) <= len(occs2) else occs1[0]
                    target_word = w1 if len(occs1) <= len(occs2) else w2
                    other_word = w2 if len(occs1) <= len(occs2) else w1

                    kata_issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=target_occ[0],
                            line=target_occ[1],
                            rule_code=self.rule_code,
                            message=(
                                f"Possible typo or spelling variance (Levenshtein distance={dist}): "
                                f"'{target_word}' vs '{other_word}' ({other_occ[0]}:{other_occ[1]})."
                            ),
                        )
                    )
                    if len(kata_issues) >= 50:
                        break
            if len(kata_issues) >= 50:
                break
        issues.extend(kata_issues)

        # English
        eng_vocab = sorted(english_occs.keys(), key=lambda x: x.lower())
        eng_issues: list[VerificationIssue] = []

        for i in range(len(eng_vocab)):
            w1 = eng_vocab[i]
            low1 = w1.lower()
            for j in range(i + 1, len(eng_vocab)):
                w2 = eng_vocab[j]
                low2 = w2.lower()
                if low1 == low2:
                    continue
                if abs(len(low1) - len(low2)) > 1:
                    continue
                if re.sub(r"\d+$", "", low1) == re.sub(r"\d+$", "", low2):
                    continue

                dist = levenshtein_distance(low1, low2)
                if dist == 1:
                    pair_key = (min(low1, low2), max(low1, low2))
                    if pair_key in reported_pairs:
                        continue
                    reported_pairs.add(pair_key)

                    occs1 = english_occs[w1]
                    occs2 = english_occs[w2]
                    target_occ = occs1[0] if len(occs1) <= len(occs2) else occs2[0]
                    other_occ = occs2[0] if len(occs1) <= len(occs2) else occs1[0]
                    target_word = w1 if len(occs1) <= len(occs2) else w2
                    other_word = w2 if len(occs1) <= len(occs2) else w1

                    eng_issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=target_occ[0],
                            line=target_occ[1],
                            rule_code=self.rule_code,
                            message=(
                                f"Possible typo or spelling variance (Levenshtein distance={dist}): "
                                f"'{target_word}' vs '{other_word}' ({other_occ[0]}:{other_occ[1]})."
                            ),
                        )
                    )
                    if len(eng_issues) >= 50:
                        break
            if len(eng_issues) >= 50:
                break
        issues.extend(eng_issues)

        return issues
