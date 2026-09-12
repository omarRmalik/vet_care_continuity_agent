"""Deterministic triage evaluator.

The language model never decides whether an emergency exists. It converts owner prose
into an :class:`Observation`; this module — pure, no I/O, no network — decides what
that means. Every fired rule is reported by id so an alert can cite exactly what
triggered it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.models import SEVERITY, Observation, TriageResult

RULES_PATH = Path(__file__).parent / "rules.yaml"

_UNSET = ("unknown", None)


class RuleSet:
    def __init__(self, rules: list[dict[str, Any]], config: dict[str, Any]):
        self.rules = rules
        self.config = config
        self._validate()

    def _validate(self) -> None:
        """Fail loudly at load time rather than silently never matching.

        The failure mode this guards against is the one the original brief shipped: a
        rule referring to a field the observation schema does not have. That rule can
        never fire, and nothing at runtime would tell you.
        """
        valid_fields = set(Observation.model_fields)
        seen_ids: set[str] = set()
        for rule in self.rules:
            rid = rule.get("id")
            if not rid:
                raise ValueError(f"rule missing id: {rule!r}")
            if rid in seen_ids:
                raise ValueError(f"duplicate rule id: {rid}")
            seen_ids.add(rid)

            field = rule.get("field")
            if field not in valid_fields:
                raise ValueError(
                    f"rule {rid} targets unknown field {field!r}; "
                    f"Observation has {sorted(valid_fields)}"
                )
            if rule.get("level") not in SEVERITY:
                raise ValueError(f"rule {rid} has invalid level {rule.get('level')!r}")
            if not any(op in rule for op in ("equals", "in", "gte", "lte")):
                raise ValueError(f"rule {rid} has no comparison operator")

        for field in self.config.get("safety_critical_fields", []):
            if field not in valid_fields:
                raise ValueError(f"safety_critical_fields references unknown field {field!r}")

    # ------------------------------------------------------------------ matching

    @staticmethod
    def _matches(rule: dict[str, Any], value: Any) -> bool:
        # An unanswered field never fires a rule. Absent data must not manufacture
        # an alert, and must not raise either.
        if value in _UNSET:
            return False

        if "equals" in rule:
            return value == rule["equals"]
        if "in" in rule:
            return value in rule["in"]
        if "gte" in rule:
            return isinstance(value, (int, float)) and value >= rule["gte"]
        if "lte" in rule:
            return isinstance(value, (int, float)) and value <= rule["lte"]
        return False

    def evaluate(
        self, obs: Observation, *, questioning_complete: bool = False
    ) -> TriageResult:
        """Evaluate an observation. Precedence is URGENT > REVIEW > NORMAL."""
        fired_ids: list[str] = []
        reasons: list[str] = []
        level = "NORMAL"

        for rule in self.rules:
            value = getattr(obs, rule["field"], None)
            if not self._matches(rule, value):
                continue
            fired_ids.append(rule["id"])
            reasons.append(rule["reason"])
            if SEVERITY[rule["level"]] > SEVERITY[level]:
                level = rule["level"]

        # Optional stricter posture: once the agent has stopped asking, a still-unknown
        # safety-critical field is itself a reason to involve a human.
        if (
            self.config.get("escalate_on_unknown")
            and questioning_complete
            and level == "NORMAL"
        ):
            unresolved = [
                f
                for f in self.config.get("safety_critical_fields", [])
                if getattr(obs, f, None) in _UNSET
            ]
            if unresolved:
                level = "REVIEW"
                fired_ids.append("R-UNK-01")
                reasons.append(
                    "Unresolved safety-critical questions: " + ", ".join(unresolved)
                )

        # Report the more severe reasons first so an alert card leads with the worst.
        order = {r["id"]: SEVERITY[r["level"]] for r in self.rules}
        order["R-UNK-01"] = SEVERITY["REVIEW"]
        paired = sorted(
            zip(fired_ids, reasons), key=lambda p: -order.get(p[0], 0)
        )
        fired_ids = [p[0] for p in paired]
        reasons = [p[1] for p in paired]

        return TriageResult(level=level, fired_rule_ids=fired_ids, reasons=reasons)


def load_ruleset(path: Path | str = RULES_PATH) -> RuleSet:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RuleSet(rules=data.get("rules", []), config=data.get("config", {}))


_default: RuleSet | None = None


def default_ruleset() -> RuleSet:
    global _default
    if _default is None:
        _default = load_ruleset()
    return _default


def evaluate(obs: Observation, *, questioning_complete: bool = False) -> TriageResult:
    """Convenience wrapper over the default ruleset."""
    return default_ruleset().evaluate(obs, questioning_complete=questioning_complete)
