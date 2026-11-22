"""Planning and reproducible assessment for an explicitly approved binary-outcome pilot."""
import math
from fractions import Fraction
from statistics import NormalDist


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, name, minimum=None, maximum=None):
    require(type(value) in (int, float) and math.isfinite(value)
            and (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum),
            f"Invalid {name}")
    return value


def validate_rules(rules):
    require(isinstance(rules, dict) and set(rules) == {
        "window_days", "lag_days", "min_activation_delta_pp",
        "max_support_ci_upper_pp", "require_positive_activation_ci",
    }, "Incomplete or unsupported pilot_rules.")
    require(type(rules["window_days"]) is int and 1 <= rules["window_days"] <= 3650
            and type(rules["lag_days"]) is int and 0 <= rules["lag_days"] <= 3650,
            "Invalid pilot observation window or reporting lag.")
    require(type(rules["require_positive_activation_ci"]) is bool,
            "require_positive_activation_ci must be boolean.")
    number(rules["min_activation_delta_pp"], "min_activation_delta_pp", 0, 100)
    number(rules["max_support_ci_upper_pp"], "max_support_ci_upper_pp", 0, 100)


def economics(activation_delta_pp, support_delta_pp, eligible, fixed_cost, support_cost, contribution=None):
    """Sensitivity for incremental contribution versus the control, in one currency."""
    activation = number(activation_delta_pp, "activation_delta_pp", -100, 100) / 100
    support = number(support_delta_pp, "support_delta_pp", -100, 100) / 100
    require(type(eligible) is int and eligible > 0, "eligible must be a positive integer.")
    fixed = number(fixed_cost, "fixed_cost", 0)
    handling = number(support_cost, "support_cost", 0)
    per_eligible_cost = fixed / eligible + support * handling
    margin = (activation * number(contribution, "contribution_per_incremental_activation")
              - per_eligible_cost) if contribution is not None else None
    return {
        "eligible": eligible,
        "expected_incremental_activations": round(eligible * activation, 4),
        "expected_incremental_organizers_with_support": round(eligible * support, 4),
        "incremental_cost_per_eligible": round(per_eligible_cost, 6),
        "break_even_contribution_per_incremental_activation":
            round(per_eligible_cost / activation, 6) if activation > 0 else None,
        "net_contribution_per_eligible": round(margin, 6) if margin is not None else None,
        "nonnegative_net_contribution": margin >= 0 if margin is not None else None,
        "assumption": "Point estimates and supplied inputs; excludes unlisted costs and uncertainty.",
    }


def plan(activation_base_pct, activation_lift_pp, support_base_pct, support_expected_pp,
         support_limit_pp, daily_eligible, window_days, lag_days, power):
    for value, name in ((activation_base_pct, "activation baseline"), (support_base_pct, "support baseline")):
        number(value, name, 0, 100)
    number(activation_lift_pp, "activation lift", 0, 100)
    number(support_expected_pp, "expected support change", -100, 100)
    number(support_limit_pp, "support limit", 0, 100)
    number(power, "power", 0.5, 0.999)
    require(type(daily_eligible) is int and daily_eligible > 0, "daily_eligible must be a positive integer.")
    require(window_days > 0 and lag_days >= 0, "Window must be positive and lag nonnegative.")
    a0, a1 = activation_base_pct / 100, (activation_base_pct + activation_lift_pp) / 100
    s0, s1 = support_base_pct / 100, (support_base_pct + support_expected_pp) / 100
    gap = (support_limit_pp - support_expected_pp) / 100
    require(0 < a0 < a1 < 1 and 0 < s0 < 1 and 0 < s1 < 1 and gap > 0,
            "Rates must be inside (0, 100), with positive lift and expected support change below its limit.")
    normal = NormalDist()
    critical, target = normal.inv_cdf(0.975), normal.inv_cdf(power)
    pooled = (a0 + a1) / 2
    activation_n = math.ceil(((critical * math.sqrt(2 * pooled * (1 - pooled))
                               + target * math.sqrt(a0 * (1 - a0) + a1 * (1 - a1))) / (a1 - a0)) ** 2)
    support_n = math.ceil((critical + target) ** 2 * (s0 * (1 - s0) + s1 * (1 - s1)) / gap**2)
    per_arm = max(activation_n, support_n)
    enrollment_days = math.ceil(2 * per_arm / daily_eligible)
    return {"activation_per_arm": activation_n, "support_per_arm": support_n,
            "recommended_per_arm": per_arm, "total": 2 * per_arm, "enrollment_days": enrollment_days,
            "days_to_complete": enrollment_days + window_days + lag_days,
            "marginal_power_target": power, "target_split": "1:1",
            "assumption": "Normal approximation, independent arms, fixed horizon; "
                          "not joint power or power to exceed a point-estimate cutoff."}


def assess(analysis, rules, costs=None):
    readiness = analysis["readiness"]
    if readiness["status"] != "ready":
        return {"verdict": "insufficient", "readiness": readiness, "checks": []}
    metrics = analysis["results"]
    activation, support = metrics["activation"], metrics["support"]
    point, lower, support_upper = (activation["delta_pp"], activation["ci95_pp"][0], support["ci95_pp"][1])
    control, test = activation["control"], activation["test"]
    point_exact = (Fraction(test["hits"], test["n"]) - Fraction(control["hits"], control["n"])) * 100
    checks = [
        {"metric": "activation_point", "observed_pp": round(point, 3), "min_pp": rules["min_activation_delta_pp"],
         "status": "pass" if point_exact >= Fraction(str(rules["min_activation_delta_pp"])) else "fail"},
        {"metric": "support_ci_upper95", "observed_pp": round(support_upper, 3), "max_pp": rules["max_support_ci_upper_pp"],
         "status": "pass" if support_upper <= rules["max_support_ci_upper_pp"] else "fail"},
    ]
    if rules["require_positive_activation_ci"]:
        checks.insert(1, {"metric": "activation_ci_lower95", "observed_pp": round(lower, 3), "exclusive_min_pp": 0,
                           "status": "pass" if lower > 0 else "fail"})
    financial = None
    if costs is not None:
        require(isinstance(costs, dict) and set(costs) == {
            "eligible", "fixed_cost", "support_cost", "contribution_per_incremental_activation"
        }, "Economics file must contain eligible, fixed_cost, support_cost and contribution_per_incremental_activation.")
        financial = economics(point, support["delta_pp"], costs["eligible"], costs["fixed_cost"],
                              costs["support_cost"], costs["contribution_per_incremental_activation"])
    sign = financial["nonnegative_net_contribution"] if financial else None
    checks.append({"metric": "net_contribution_scenario",
                   "status": "unknown" if sign is None else "pass" if sign else "fail",
                   "basis": "User-supplied scenario; confirm inputs before approval."})
    verdict = "stop" if any(c["status"] == "fail" for c in checks) else (
        "insufficient" if any(c["status"] == "unknown" for c in checks) else "review")
    displayed = {key: values | {"delta_pp": round(values["delta_pp"], 3),
                                "ci95_pp": [round(bound, 3) for bound in values["ci95_pp"]]}
                 for key, values in metrics.items()}
    return {"verdict": verdict, "readiness": readiness, "checks": checks,
            "economics": financial, "metrics": displayed,
            "manual_checks": ["Confirm source completeness, billing incidents, real unit economics and approval."]}
