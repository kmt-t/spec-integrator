from __future__ import annotations

import contextlib
import importlib.util
import io
from dataclasses import dataclass, field
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.verifier.static import VerificationIssue

# Only a *universal* eventuality expresses progress: AF q means every path reaches q.
# EF q merely means q is reachable, which an execution that loops forever without ever
# taking that branch still satisfies. A liveness claim written with EF proves
# possibility, not inevitability, and is not a progress guarantee.
#
# In pyModelChecking, AF/EF/AG/... are *functions*, not classes: at runtime AF(x)
# is A(F(x)) and EF(x) is E(F(x)). The quantifier therefore has to be read from the
# parent node, not from the operator's own type name.
PATH_QUANTIFIERS = ("A", "E")
EVENTUALITY_OPERATORS = ("F", "U")
LIVENESS_KINDS = ("liveness", "response", "deadlock_freedom", "progress")


@dataclass
class PropertyResult:
    name: str
    kind: str
    status: str  # "PASS", "FAIL", "VACUOUS", "INVALID"
    details: str = ""


@dataclass
class FormalModelResult:
    component: str
    model_file: str
    status: str  # "PASS", "FAIL", "ERROR", "NOT_FOUND", "NO_CONTRACT", "VACUOUS"
    details: str = ""
    invariants: list[dict] = field(default_factory=list)
    properties: list[PropertyResult] = field(default_factory=list)
    backing_documents: list[str] = field(default_factory=list)
    audit: dict = field(default_factory=dict)


class ModelContractError(Exception):
    """Raised when a formal model does not expose the auditable contract."""


def _collect_atomic_propositions(formula) -> set[str]:
    """Recursively collects atomic proposition names appearing in a formula."""
    names: set[str] = set()
    stack = [formula]
    seen = 0
    while stack and seen < 10000:
        node = stack.pop()
        seen += 1
        if node is None:
            continue
        if isinstance(node, str):
            names.add(node)
            continue
        try:
            subs = node.subformulas()
        except Exception:
            subs = []
        if subs:
            stack.extend(list(subs))
            continue
        name = getattr(node, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _derive_violation(formula):
    """For a safety property shaped AG(Not(phi)), returns phi (the state to be excluded)."""
    try:
        if type(formula).__name__ not in ("AG", "G"):
            return None
        inner = formula.subformulas()
        if len(inner) != 1:
            return None
        negation = inner[0]
        if type(negation).__name__ != "Not":
            return None
        neg_sub = negation.subformulas()
        if len(neg_sub) != 1:
            return None
        return neg_sub[0]
    except Exception:
        return None


def _eventuality_quantifier(formula) -> str | None:
    """Returns 'universal', 'existential', or None for the strongest eventuality used."""
    found: set[str] = set()
    stack: list[tuple[object, str | None]] = [(formula, None)]
    seen = 0
    while stack and seen < 10000:
        node, enclosing = stack.pop()
        seen += 1
        if node is None:
            continue
        name = type(node).__name__
        if name in EVENTUALITY_OPERATORS:
            if enclosing == "E":
                found.add("existential")
            else:
                # A(F ...) is universal; a bare F/U is LTL, where all paths are implied.
                found.add("universal")

        child_quantifier = name if name in PATH_QUANTIFIERS else None
        try:
            for sub in node.subformulas():
                stack.append((sub, child_quantifier))
        except Exception:
            pass

    if "universal" in found:
        return "universal"
    if "existential" in found:
        return "existential"
    return None


class FormalVerifier:
    def __init__(self, config: Config):
        self.config = config

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def verify_documents(
        self, documents: list[ParsedDocument], docs_root: Path
    ) -> tuple[list[VerificationIssue], list[FormalModelResult]]:
        issues: list[VerificationIssue] = []
        results: list[FormalModelResult] = []
        fv = self.config.formal_verification
        formal_tag = fv.tag
        model_dir_name = fv.model_dir_name
        # A model file is executed exactly once even when several documents share
        # the same formal/ directory, so the report cannot inflate the model count.
        ran_models: dict[str, FormalModelResult] = {}
        # formal dir -> documents demanding verification from it
        dir_claimants: dict[str, list[ParsedDocument]] = {}
        tagged_docs = [d for d in documents if formal_tag in d.all_tags]
        # Pre-scan all formal model scripts across the repository to build BACKS index
        all_model_files = sorted(docs_root.glob(f"**/{model_dir_name}/*.py"))
        all_model_files = [m for m in all_model_files if not m.name.startswith("_")]
        doc_to_backing_models: dict[str, list[Path]] = {}
        for mf in all_model_files:
            try:
                mod, _ = self._load_module(mf)
                backs = self._load_backs(mod)
                for b in backs:
                    doc_to_backing_models.setdefault(b, []).append(mf)
            except Exception:
                pass

        for doc in tagged_docs:
            if doc.file_path in doc_to_backing_models:
                for mf in doc_to_backing_models[doc.file_path]:
                    dir_claimants.setdefault(str(mf.parent), []).append(doc)
            else:
                cur = doc.full_path.parent
                resolved_formal_dir = doc.full_path.parent / model_dir_name
                while cur and cur != docs_root.parent and cur != docs_root:
                    cand_dir = cur / model_dir_name
                    if cand_dir.exists():
                        files = [m for m in cand_dir.glob("*.py") if not m.name.startswith("_")]
                        if files:
                            resolved_formal_dir = cand_dir
                            break
                    cur = cur.parent
                dir_claimants.setdefault(str(resolved_formal_dir), []).append(doc)

        for doc in tagged_docs:
            model_files = []
            if doc.file_path in doc_to_backing_models:
                model_files = doc_to_backing_models[doc.file_path]
            else:
                cur = doc.full_path.parent
                doc.full_path.parent / model_dir_name
                while cur and cur != docs_root.parent and cur != docs_root:
                    cand_dir = cur / model_dir_name
                    if cand_dir.exists():
                        files = sorted(cand_dir.glob("*.py"))
                        files = [m for m in files if not m.name.startswith("_")]
                        if files:
                            model_files = files
                            break
                    cur = cur.parent

            if not model_files:
                rel_dir = self._rel(doc.full_path.parent / model_dir_name, docs_root)
                issues.append(
                    VerificationIssue(
                        gate="Formal",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code="FORMAL-MODEL-NOT-FOUND",
                        message=(
                            f"Document declares '{formal_tag}' but no formal model script exists "
                            f"in '{rel_dir}/' or references it via BACKS. A verification claim without a model is not admissible."
                        ),
                    )
                )
                results.append(
                    FormalModelResult(
                        component=doc.component,
                        model_file=rel_dir,
                        status="NOT_FOUND",
                        details="No formal model script (*.py) found.",
                        backing_documents=[doc.file_path],
                    )
                )
                continue

            for model_file in model_files:
                key = str(model_file.resolve())
                if key in ran_models:
                    res = ran_models[key]
                    if doc.file_path not in res.backing_documents:
                        res.backing_documents.append(doc.file_path)
                    continue

                res = self._audit_model(model_file, doc.component, docs_root)
                res.backing_documents = [doc.file_path]
                ran_models[key] = res
                results.append(res)
                issues.extend(self._issues_for(res, model_file, docs_root))

        # Several documents lean on the same directory but no model states which
        # claim it discharges -> the backing is ambiguous and cannot be audited.
        issues.extend(self._verify_backing_attribution(dir_claimants, ran_models, docs_root))
        return issues, results

    # ------------------------------------------------------------------ #
    # Model audit
    # ------------------------------------------------------------------ #
    def _audit_model(self, script_path: Path, component: str, docs_root: Path) -> FormalModelResult:
        rel_path = self._rel(script_path, docs_root)
        fv = self.config.formal_verification
        try:
            module, output = self._load_module(script_path)
        except Exception as e:
            return FormalModelResult(
                component=component,
                model_file=rel_path,
                status="ERROR",
                details=f"Execution error: {e}",
            )

        # --- Legacy self-reported result (kept for the report text only) ---
        legacy_output = output
        legacy_pass = True
        if hasattr(module, "verify"):
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ret = module.verify()
                legacy_output = buf.getvalue().strip() or legacy_output
                legacy_pass = (
                    ret == 0
                    or ret is None
                    or (isinstance(ret, dict) and ret.get("status") == "PASS")
                )
            except Exception as e:
                return FormalModelResult(
                    component=component,
                    model_file=rel_path,
                    status="ERROR",
                    details=f"verify() raised: {e}",
                )

        if not fv.require_contract:
            return FormalModelResult(
                component=component,
                model_file=rel_path,
                status="PASS" if legacy_pass else "FAIL",
                details=legacy_output or "Model checking finished.",
            )

        # --- Auditable contract ---
        try:
            model = self._build_model(module)
            props = self._load_properties(module)
        except ModelContractError as e:
            return FormalModelResult(
                component=component,
                model_file=rel_path,
                status="NO_CONTRACT",
                details=str(e),
            )
        except Exception as e:
            return FormalModelResult(
                component=component,
                model_file=rel_path,
                status="ERROR",
                details=f"Contract evaluation failed: {e}",
            )

        audit = self._audit_structure(model, props)
        audit["backs"] = self._load_backs(module)
        prop_results = [self._audit_property(model, p, audit) for p in props]
        prop_results = self._audit_guard_effectiveness(module, props, prop_results)
        failed = [p for p in prop_results if p.status not in ("PASS", "REFUTED")]
        if audit.get("errors"):
            status = "FAIL"
        elif failed:
            status = "VACUOUS" if all(p.status == "VACUOUS" for p in failed) else "FAIL"
        else:
            status = "PASS"

        detail_bits = [legacy_output] if legacy_output else []
        detail_bits.append(
            f"{len(prop_results)} propert(y/ies) audited; "
            f"{len(model.states())} states, "
            f"{audit.get('reachable_count', 0)} reachable, "
            f"branching={audit.get('max_branching', 0)}"
        )
        return FormalModelResult(
            component=component,
            model_file=rel_path,
            status=status,
            details=" | ".join(b for b in detail_bits if b),
            properties=prop_results,
            audit=audit,
        )

    def _load_module(self, script_path: Path):
        spec = importlib.util.spec_from_file_location(
            f"formal_{script_path.stem}", str(script_path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load module spec from {script_path}")
        module = importlib.util.module_from_spec(spec)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            spec.loader.exec_module(module)
        return module, buf.getvalue().strip()

    def _build_model(self, module):
        builder = getattr(module, "build_model", None)
        if builder is None:
            candidates = [
                getattr(module, n)
                for n in dir(module)
                if n.startswith("build_") and callable(getattr(module, n))
            ]
            if len(candidates) == 1:
                builder = candidates[0]
        if builder is None or not callable(builder):
            raise ModelContractError(
                "Model does not expose 'build_model()'. The auditor cannot inspect the state "
                "space, so the model's claim is unverifiable. Define "
                "'def build_model() -> Kripke'."
            )
        model = builder()
        if not hasattr(model, "states") or not hasattr(model, "next"):
            raise ModelContractError(
                "build_model() must return a pyModelChecking Kripke structure."
            )
        return model

    def _load_properties(self, module) -> list[dict]:
        props = getattr(module, "properties", None)
        if callable(props):
            props = props()
        if props is None:
            props = getattr(module, "PROPERTIES", None)
        if not props:
            raise ModelContractError(
                "Model does not expose 'properties()'. Each checked property must declare "
                "{'name', 'kind', 'formula', and (for safety) 'violation'} so that the auditor can "
                "prove the property is falsifiable. A property that cannot fail is not a proof."
            )
        if not isinstance(props, (list, tuple)):
            raise ModelContractError("properties() must return a list of property descriptors.")
        return list(props)

    def _audit_guard_effectiveness(
        self, module, props: list[dict], results: list[PropertyResult]
    ) -> list[PropertyResult]:
        """Mutation test: a violation must be unreachable *because a guard prevents it*.
        A safety proof claims the design keeps the bad state out of reach. But a model
        in which the bad state simply has no incoming edge proves exactly the same
        formula while encoding no protection at all — the modeller merely omitted the
        transition. The two are structurally indistinguishable.
        So the model must expose its protection as something that can be switched off:
        `build_model(guards=False)`. With the guard disabled the violation has to become
        reachable. If it does not, the guard is doing no work and the property holds by
        omission rather than by design.
        """
        if not self.config.formal_verification.check_guard_effectiveness:
            return results

        # Only properties that *claim protection* need a guard.
        claims = [
            (i, p)
            for i, p in enumerate(props)
            if p.get("expect", True)
            and (p.get("violation") is not None or _derive_violation(p.get("formula")) is not None)
        ]
        if not claims:
            return results

        builder = getattr(module, "build_model", None)
        if builder is None:
            return results

        import inspect

        try:
            accepts_guards = "guards" in inspect.signature(builder).parameters
        except (TypeError, ValueError):
            accepts_guards = False

        if not accepts_guards:
            for i, p in claims:
                if results[i].status != "PASS":
                    continue  # already rejected for a more fundamental reason
                results[i] = PropertyResult(
                    str(p.get("name", "<unnamed>")),
                    str(p.get("kind", "safety")),
                    "INVALID",
                    "claims the design prevents this violation, but the model offers no way to "
                    "test that claim. Define 'build_model(*, guards: bool = True)' so the "
                    "protection can be switched off: with guards=False the violation must become "
                    "reachable. Otherwise the property holds because the transition was never "
                    "drawn, not because the design prevents it",
                )
            return results

        try:
            unguarded = builder(guards=False)
        except Exception as e:
            for i, p in claims:
                if results[i].status != "PASS":
                    continue
                results[i] = PropertyResult(
                    str(p.get("name", "<unnamed>")),
                    str(p.get("kind", "safety")),
                    "INVALID",
                    f"build_model(guards=False) raised: {e}",
                )
            return results

        try:
            reachable = set(unguarded.get_reachable_set_from(set(unguarded.S0)))
        except Exception:
            reachable = set(unguarded.states())

        for i, p in claims:
            if results[i].status != "PASS":
                continue
            violation = p.get("violation") or _derive_violation(p.get("formula"))
            modelcheck = self._resolve_modelcheck(p, p.get("formula"))
            if modelcheck is None:
                continue
            try:
                bad_states = set(modelcheck(unguarded, violation)) & reachable
            except Exception as e:
                results[i] = PropertyResult(
                    results[i].name,
                    results[i].kind,
                    "INVALID",
                    f"violation could not be checked on the unguarded model: {e}",
                )
                continue

            if not bad_states:
                results[i] = PropertyResult(
                    results[i].name,
                    results[i].kind,
                    "INVALID",
                    "the violation stays unreachable even with guards disabled, so the guard "
                    "prevents nothing. The property holds because the transition leading to the "
                    "violation is absent from the model, not because the design blocks it — "
                    "model the path that would cause the violation, then show the guard cuts it",
                )
            else:
                results[i] = PropertyResult(
                    results[i].name,
                    results[i].kind,
                    "PASS",
                    f"{results[i].details}; guard verified by mutation "
                    f"(violation reachable in {len(bad_states)} state(s) when disabled)",
                )
        return results

    @staticmethod
    def _load_backs(module) -> list[str]:
        """Documents whose verification claim this model discharges."""
        backs = getattr(module, "backs", None)
        if callable(backs):
            try:
                backs = backs()
            except Exception:
                backs = None
        if backs is None:
            backs = getattr(module, "BACKS", None)
        if not backs:
            return []
        if isinstance(backs, str):
            backs = [backs]
        return [str(b).replace("\\", "/").lstrip("./") for b in backs]

    # ------------------------------------------------------------------ #
    # Structural audit
    # ------------------------------------------------------------------ #
    def _audit_structure(self, model, props: list[dict] | None = None) -> dict:
        fv = self.config.formal_verification
        states = list(model.states())
        errors: list[str] = []
        try:
            reachable = set(model.get_reachable_set_from(set(model.S0)))
        except Exception:
            reachable = set(states)

        unreachable = set(states) - reachable
        # Allow unreachable states ONLY if they represent a declared safety violation
        # of a property with expect: True (i.e. the protection mechanism intentionally
        # leaves the bad state unreachable, proving safety by construction).
        declared_violations: set[str] = set()
        if props:
            for p in props:
                if p.get("expect", True):
                    viol = p.get("violation")
                    if viol is not None:
                        try:
                            mc = self._resolve_modelcheck(p, viol)
                            if mc:
                                declared_violations |= {str(x) for x in mc(model, viol)}
                        except Exception:
                            pass

        unexplained = sorted(str(s) for s in (unreachable - declared_violations))
        max_branching = 0
        for s in reachable:
            try:
                succ = set(model.next(s)) - {s}
            except Exception:
                succ = set()
            max_branching = max(max_branching, len(succ))

        if fv.check_reachability and unexplained:
            errors.append(
                f"states unreachable from S0: {', '.join(unexplained)} "
                "(the transition relation does not match the drawn state machine)"
            )

        if len(states) < fv.min_states:
            errors.append(
                f"the model has only {len(states)} state(s) (minimum {fv.min_states}); "
                "it is too coarse to represent the behaviour it claims to verify"
            )

        if fv.check_nondeterminism and max_branching < 2:
            errors.append(
                "every reachable state has at most one distinct successor, so the model is a single "
                "deterministic path: it cannot exhibit interleaving, races, deadlock or starvation, "
                "and any concurrency claim proved over it is vacuous"
            )

        return {
            "state_count": len(states),
            "reachable_count": len(reachable),
            "unreachable": unreachable,
            "max_branching": max_branching,
            "errors": errors,
        }

    # ------------------------------------------------------------------ #
    # Per-property audit
    # ------------------------------------------------------------------ #
    def _audit_property(self, model, prop: dict, audit: dict) -> PropertyResult:
        name = str(prop.get("name", "<unnamed>"))
        kind = str(prop.get("kind", "safety")).lower()
        formula = prop.get("formula")
        expect = bool(prop.get("expect", True))
        if formula is None:
            return PropertyResult(name, kind, "INVALID", "property descriptor has no 'formula'")

        modelcheck = self._resolve_modelcheck(prop, formula)
        if modelcheck is None:
            return PropertyResult(
                name,
                kind,
                "INVALID",
                "cannot resolve a model checker for this formula; "
                "declare 'logic': 'CTL' or 'LTL' in the descriptor",
            )

        # 1. Every atomic proposition must actually occur in the labelling.
        label_universe: set[str] = set()
        for s in model.states():
            label_universe |= {str(x) for x in model.labels(s)}
        used = _collect_atomic_propositions(formula)
        orphan = sorted(a for a in used if a not in label_universe)
        if orphan:
            return PropertyResult(
                name,
                kind,
                "INVALID",
                f"atomic proposition(s) {', '.join(orphan)} never appear in any state label; "
                "the property refers to a condition the model cannot express",
            )

        # 2. Kind / operator consistency.
        if kind in LIVENESS_KINDS:
            quantifier = _eventuality_quantifier(formula)
            if quantifier is None:
                return PropertyResult(
                    name,
                    kind,
                    "INVALID",
                    f"declared as '{kind}' but the formula contains no eventuality operator "
                    "(AF/AU/F/U); an invariant cannot express a liveness claim",
                )
            if quantifier == "existential":
                return PropertyResult(
                    name,
                    kind,
                    "INVALID",
                    f"declared as '{kind}' but progress is expressed with an existential "
                    "eventuality (EF/EU). 'EF q' only says q is *reachable*; an execution that "
                    "loops forever without ever taking that branch still satisfies it. Use a "
                    "universal eventuality (AF q) to claim q is inevitable",
                )

        # 3. Falsifiability: the violating condition must be representable.
        if self.config.formal_verification.check_vacuity:
            violation = prop.get("violation")
            if violation is None:
                violation = _derive_violation(formula)
            if violation is None:
                if kind in ("safety", "deadlock_freedom", "mutual_exclusion"):
                    return PropertyResult(
                        name,
                        kind,
                        "INVALID",
                        "no 'violation' formula declared and none derivable from the property shape. "
                        "State the condition that would falsify this property so vacuity can be ruled out",
                    )
            else:
                try:
                    sat_violation = modelcheck(model, violation)
                except Exception as e:
                    return PropertyResult(
                        name,
                        kind,
                        "INVALID",
                        f"violation formula could not be checked: {e}",
                    )
                if not set(sat_violation):
                    return PropertyResult(
                        name,
                        kind,
                        "VACUOUS",
                        f"no state in the model can satisfy the violating condition '{violation}'. "
                        "The property holds by construction of the state space, not because the "
                        "design prevents the violation — this is not a proof",
                    )

        # 4. A property the model deliberately refutes states a limitation of the
        #    design, so the author must write down what that limitation is. Without
        #    it, "expect: False" quietly reads as a pass in every summary.
        if not expect and not str(prop.get("refutation_note", "")).strip():
            return PropertyResult(
                name,
                kind,
                "INVALID",
                "declared 'expect: False' without a 'refutation_note'. The model asserts this "
                "property does NOT hold, which is a statement about the design's limits — "
                "state it explicitly so the specification cannot claim the opposite",
            )

        # 5. Actually check the property.
        try:
            sat = set(modelcheck(model, formula))
        except Exception as e:
            return PropertyResult(name, kind, "INVALID", f"model checking raised: {e}")

        holds = set(model.S0).issubset(sat)
        if holds == expect:
            if not expect:
                return PropertyResult(
                    name,
                    kind,
                    "REFUTED",
                    f"refuted as expected — {prop.get('refutation_note', '')}",
                )
            return PropertyResult(name, kind, "PASS", "holds at all initial states")
        return PropertyResult(
            name,
            kind,
            "FAIL",
            f"expected the property to {'hold' if expect else 'be refuted'}, but it "
            f"{'holds' if holds else 'does not hold'} at the initial states",
        )

    @staticmethod
    def _resolve_modelcheck(prop: dict, formula):
        checker = prop.get("modelcheck")
        if callable(checker):
            return checker
        logic = str(prop.get("logic", "")).upper()
        try:
            if logic == "LTL":
                from pyModelChecking.LTL import modelcheck as mc

                return mc
            if logic == "CTL":
                from pyModelChecking.CTL import modelcheck as mc

                return mc
            module_name = type(formula).__module__ or ""
            if ".LTL" in module_name:
                return mc

            return mc
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Issue construction
    # ------------------------------------------------------------------ #
    def _issues_for(
        self, res: FormalModelResult, model_file: Path, docs_root: Path
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        loc = self._rel(model_file, docs_root)

        def add(code: str, msg: str):
            issues.append(
                VerificationIssue(
                    gate="Formal",
                    severity="ERROR",
                    file_path=loc,
                    line=1,
                    rule_code=code,
                    message=msg,
                )
            )

        if res.status == "NO_CONTRACT":
            add(
                "FORMAL-MODEL-NO-CONTRACT",
                f"Model '{model_file.name}' is not auditable: {res.details}",
            )
            return issues

        if res.status == "ERROR":
            add(
                "FORMAL-MODEL-ERROR",
                f"Model '{model_file.name}' failed to run: {res.details}",
            )
            return issues

        for err in res.audit.get("errors", []):
            add("FORMAL-MODEL-UNSOUND", f"Model '{model_file.name}': {err}")

        for p in res.properties:
            if p.status == "VACUOUS":
                add(
                    "FORMAL-PROPERTY-VACUOUS",
                    f"Property '{p.name}' in '{model_file.name}' is vacuously true: {p.details}",
                )
            elif p.status == "INVALID":
                add(
                    "FORMAL-PROPERTY-INVALID",
                    f"Property '{p.name}' in '{model_file.name}' is not admissible: {p.details}",
                )
            elif p.status == "REFUTED":
                issues.append(
                    VerificationIssue(
                        gate="Formal",
                        severity="WARNING",
                        file_path=loc,
                        line=1,
                        rule_code="FORMAL-PROPERTY-REFUTED",
                        message=(
                            f"Model '{model_file.name}' REFUTES '{p.name}'. "
                            f"{p.details} Confirm that no document backed by this model "
                            f"claims the property holds: "
                            f"{', '.join(res.backing_documents) or '(none declared)'}"
                        ),
                    )
                )
            elif p.status == "FAIL":
                add(
                    "FORMAL-VERIFICATION-FAILED",
                    f"Property '{p.name}' in '{model_file.name}' failed: {p.details}",
                )

        return issues

    def _verify_backing_attribution(
        self,
        dir_claimants: dict[str, list[ParsedDocument]],
        ran_models: dict[str, FormalModelResult],
        docs_root: Path,
    ) -> list[VerificationIssue]:
        """Two documents sharing one formal/ dir must each name the model that discharges them."""
        issues: list[VerificationIssue] = []
        for dir_path, docs in dir_claimants.items():
            if len(docs) < 2:
                continue
            declared: set[str] = set()
            for key, res in ran_models.items():
                if not key.startswith(str(Path(dir_path).resolve())):
                    continue
                declared |= set(res.audit.get("backs", []) or [])
            for doc in docs:
                if any(
                    d == doc.file_path
                    or d.endswith("/" + doc.file_path)
                    or doc.file_path.endswith("/" + d)
                    for d in declared
                ):
                    continue
                issues.append(
                    VerificationIssue(
                        gate="Formal",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code="FORMAL-BACKING-AMBIGUOUS",
                        message=(
                            f"{len(docs)} documents declare '{self.config.formal_verification.tag}' against the "
                            f"same '{Path(dir_path).name}/' directory, but no model declares that it discharges "
                            f"'{doc.file_path}'. Add 'BACKS = [\"{doc.file_path}\"]' to the model that proves it, "
                            "otherwise one model is being counted as proof for several unrelated claims."
                        ),
                    )
                )
        return issues

    @staticmethod
    def _rel(path: Path, docs_root: Path) -> str:
        try:
            return path.relative_to(docs_root).as_posix()
        except ValueError:
            return path.as_posix()
