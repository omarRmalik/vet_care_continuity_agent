"""Triage engine tests.

The first test is the important one: the scenario the original brief leads with
(increasing swelling + yellow discharge -> REVIEW) returns NORMAL under the brief's
own `evaluate_followup`, because its rules and its data schema share no field names.
"""

from __future__ import annotations

import pytest

from core.models import Observation
from core.rules import RuleSet, evaluate, load_ruleset


# --------------------------------------------------------------- the headline case


def test_increasing_swelling_with_yellow_discharge_escalates_to_review():
    obs = Observation(
        appetite="normal",
        drinking="normal",
        medication_given="yes",
        incision_bleeding="none",
        incision_swelling="increasing",
        incision_discharge="yellow",
    )
    result = evaluate(obs)

    assert result.level == "REVIEW"
    assert set(result.fired_rule_ids) == {"R-REV-01", "R-REV-02"}
    assert result.needs_attention is True
    assert result.is_urgent is False


# ------------------------------------------------------------------ missing data


def test_empty_observation_is_normal_and_does_not_raise():
    # Every field unknown, vomiting_count None. The brief's version raised KeyError.
    result = evaluate(Observation())
    assert result.level == "NORMAL"
    assert result.fired_rule_ids == []


def test_unknown_never_fires_a_rule():
    for field in Observation.model_fields:
        obs = Observation()
        assert getattr(obs, field) in ("unknown", None)
    assert evaluate(Observation()).level == "NORMAL"


def test_all_unknown_fields_are_reported():
    obs = Observation(appetite="normal")
    unknown = obs.unknown_fields()
    assert "appetite" not in unknown
    assert "incision_bleeding" in unknown
    assert "vomiting_count" in unknown


# ----------------------------------------------------------------------- URGENT


@pytest.mark.parametrize(
    "field,value,rule_id",
    [
        ("incision_bleeding", "heavy", "R-URG-01"),
        ("breathing", "labored", "R-URG-02"),
        ("incision_open", "yes", "R-URG-03"),
        ("pain_signs", "severe", "R-URG-04"),
    ],
)
def test_each_urgent_rule_fires(field, value, rule_id):
    result = evaluate(Observation(**{field: value}))
    assert result.level == "URGENT"
    assert rule_id in result.fired_rule_ids


@pytest.mark.parametrize(
    "field,value",
    [
        ("incision_bleeding", "minor"),
        ("breathing", "normal"),
        ("incision_open", "no"),
        ("pain_signs", "mild"),
    ],
)
def test_urgent_rules_do_not_overfire(field, value):
    assert evaluate(Observation(**{field: value})).level == "NORMAL"


# ----------------------------------------------------------------------- REVIEW


@pytest.mark.parametrize(
    "field,value,rule_id",
    [
        ("incision_swelling", "increasing", "R-REV-01"),
        ("incision_discharge", "yellow", "R-REV-02"),
        ("incision_discharge", "green", "R-REV-02"),
        ("appetite", "none", "R-REV-03"),
        ("drinking", "none", "R-REV-04"),
        ("medication_given", "no", "R-REV-06"),
        ("demeanor", "lethargic", "R-REV-07"),
        ("demeanor", "distressed", "R-REV-07"),
    ],
)
def test_each_review_rule_fires(field, value, rule_id):
    result = evaluate(Observation(**{field: value}))
    assert result.level == "REVIEW"
    assert rule_id in result.fired_rule_ids


@pytest.mark.parametrize(
    "count,expected",
    [(None, "NORMAL"), (0, "NORMAL"), (2, "NORMAL"), (3, "REVIEW"), (7, "REVIEW")],
)
def test_vomiting_count_threshold(count, expected):
    assert evaluate(Observation(vomiting_count=count)).level == expected


# -------------------------------------------------------------- the YAML bool trap


def test_yes_no_enum_values_are_strings_not_booleans():
    """`equals: yes` unquoted is a YAML boolean and would never match the string
    enum. These two rules are the canaries."""
    assert evaluate(Observation(incision_open="yes")).level == "URGENT"
    assert evaluate(Observation(medication_given="no")).level == "REVIEW"
    # And the benign values must not fire.
    assert evaluate(Observation(incision_open="no")).level == "NORMAL"
    assert evaluate(Observation(medication_given="yes")).level == "NORMAL"


# -------------------------------------------------------------------- precedence


def test_urgent_outranks_review():
    obs = Observation(
        incision_swelling="increasing",  # REVIEW
        incision_discharge="yellow",  # REVIEW
        incision_bleeding="heavy",  # URGENT
    )
    result = evaluate(obs)
    assert result.level == "URGENT"
    assert {"R-URG-01", "R-REV-01", "R-REV-02"} <= set(result.fired_rule_ids)


def test_most_severe_reason_is_listed_first():
    obs = Observation(incision_swelling="increasing", incision_bleeding="heavy")
    result = evaluate(obs)
    assert result.fired_rule_ids[0] == "R-URG-01"


def test_multiple_reasons_are_all_reported():
    obs = Observation(appetite="none", drinking="none", demeanor="lethargic")
    result = evaluate(obs)
    assert len(result.reasons) == 3
    assert len(result.fired_rule_ids) == 3


# ------------------------------------------------------- escalate_on_unknown flag


def _ruleset_with(**config_overrides) -> RuleSet:
    base = load_ruleset()
    config = {**base.config, **config_overrides}
    return RuleSet(rules=base.rules, config=config)


def test_unknown_safety_fields_pass_as_normal_when_flag_is_off():
    rs = _ruleset_with(escalate_on_unknown=False)
    result = rs.evaluate(Observation(appetite="normal"), questioning_complete=True)
    assert result.level == "NORMAL"


def test_unknown_safety_fields_escalate_when_flag_is_on():
    rs = _ruleset_with(escalate_on_unknown=True)
    result = rs.evaluate(Observation(appetite="normal"), questioning_complete=True)
    assert result.level == "REVIEW"
    assert "R-UNK-01" in result.fired_rule_ids
    assert "incision_bleeding" in result.reasons[0]


def test_unknown_escalation_does_not_fire_mid_questioning():
    rs = _ruleset_with(escalate_on_unknown=True)
    result = rs.evaluate(Observation(appetite="normal"), questioning_complete=False)
    assert result.level == "NORMAL"


def test_unknown_escalation_does_not_mask_a_real_rule():
    rs = _ruleset_with(escalate_on_unknown=True)
    result = rs.evaluate(
        Observation(incision_bleeding="heavy"), questioning_complete=True
    )
    assert result.level == "URGENT"
    assert "R-UNK-01" not in result.fired_rule_ids


# ------------------------------------------------------------ config validation


def test_rule_referencing_a_nonexistent_field_is_rejected_at_load():
    """This is precisely the defect in the source document: rules keyed on
    `heavy_bleeding` while the schema produces `bleeding`."""
    with pytest.raises(ValueError, match="unknown field"):
        RuleSet(
            rules=[
                {
                    "id": "R-BAD-01",
                    "field": "heavy_bleeding",
                    "equals": True,
                    "level": "URGENT",
                    "reason": "…",
                }
            ],
            config={},
        )


def test_duplicate_rule_ids_are_rejected():
    rule = {
        "id": "R-DUP",
        "field": "appetite",
        "equals": "none",
        "level": "REVIEW",
        "reason": "…",
    }
    with pytest.raises(ValueError, match="duplicate rule id"):
        RuleSet(rules=[rule, dict(rule)], config={})


def test_rule_without_operator_is_rejected():
    with pytest.raises(ValueError, match="no comparison operator"):
        RuleSet(
            rules=[
                {"id": "R-NOOP", "field": "appetite", "level": "REVIEW", "reason": "…"}
            ],
            config={},
        )


def test_shipped_ruleset_loads_clean():
    rs = load_ruleset()
    assert len(rs.rules) >= 10
    assert {r["id"] for r in rs.rules} >= {"R-URG-01", "R-REV-01", "R-REV-02"}


# --------------------------------------------------------------- observation merge


def test_merge_refines_without_blanking_established_fields():
    established = Observation(appetite="normal", incision_swelling="mild")
    updated = established.merge({"incision_swelling": "increasing", "appetite": "unknown"})
    assert updated.incision_swelling == "increasing"
    assert updated.appetite == "normal"  # not clobbered by an unknown


def test_merge_accepts_an_observation_instance():
    established = Observation(appetite="normal")
    updated = established.merge(Observation(incision_discharge="yellow"))
    assert updated.appetite == "normal"
    assert updated.incision_discharge == "yellow"
