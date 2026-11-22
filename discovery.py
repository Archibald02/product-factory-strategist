"""Structured review of communication claims, interview observations and event maps."""
from collections import Counter, defaultdict

from decision_map import plain


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(payload, key):
    values = payload.get(key)
    require(isinstance(values, list) and values and all(isinstance(item, dict) for item in values),
            f"{key} must be a nonempty list of objects.")
    return values


def indexed(values, key):
    result = {}
    for item in values:
        identifier = item.get(key)
        require(isinstance(identifier, str) and identifier.strip() and identifier not in result,
                f"Missing or duplicate {key}: {identifier}")
        result[identifier] = item
    return result


def document_line(root, ref):
    name, delimiter, number = ref.rpartition(":")
    require(delimiter and number.isdecimal() and int(number) > 0, "Use a file:line locator.")
    path = (root / name).resolve()
    require(path.is_relative_to(root) and path.suffix in {".html", ".md", ".txt"},
            "Locator must point to a text document inside the bundle.")
    lines = path.read_text(encoding="utf-8").splitlines()
    require(int(number) <= len(lines), f"Locator is past end of file: {ref}")
    return lines[int(number) - 1]


def audit_copy(payload, root):
    scope = payload["scope"]
    require(scope in {"study_fixture", "project"}, "Specify scope as study_fixture or project.")
    verdicts = {"supported", "needs_condition", "unverified", "contradicted"}
    claims = []
    for key, row in indexed(rows(payload, "claims"), "id").items():
        verdict = row["judgment"]
        require(verdict in verdicts and row["screen"].strip() and row["phrase"].strip()
                and row["reason"].strip() and row["proposed_copy"].strip(), f"Incomplete claim: {key}")
        line = document_line(root, row["copy_ref"])
        require(row["phrase"] in line, f"Phrase differs from screen text: {key}")
        evidence = row["evidence"]
        require(isinstance(evidence, list) and (evidence or verdict == "unverified"),
                f"Add evidence or label the claim unverified: {key}")
        for ref in evidence:
            require(document_line(root, ref).strip(), f"Blank evidence locator: {ref}")
        claims.append({"id": key, "screen": row["screen"], "phrase": row["phrase"],
                       "copy_ref": row["copy_ref"], "evidence": evidence, "judgment": verdict,
                       "reason": row["reason"], "proposed_copy": row["proposed_copy"]})
    return {"scope": scope, "screen_count": len({row["screen"] for row in claims}),
            "requires_review": [row["id"] for row in claims if row["judgment"] != "supported"],
            "claims": claims, "checked": "Locators and exact words; evidence judgments were supplied by the reviewer."}


def interview_guide(payload):
    lines = [f"# Интервью про {plain(payload['subject'])}", "",
             "**Цель.** Проверить понимание до показа правильного ответа. Спроси согласие, "
             "не записывай номера, имена и другие идентификаторы.", ""]
    for key, task in indexed(rows(payload, "tasks"), "id").items():
        lines += [f"## {plain(key)} · {plain(task['role'])}", "",
                  f"- **Дай задание:** {plain(task['prompt'])}",
                  f"- **Уточни:** «Как вы это поняли? Покажите, где это видно».",
                  f"- **Ожидаемое понимание:** {plain(task['expected'])}",
                  f"- **Критическая ошибка:** {plain(task['critical_error'])}",
                  f"- **Код:** `pass`, `unclear` или `critical`.", ""]
    lines += ["После каждого задания сохраните обезличенный код, роль, короткое наблюдение и версию макета. "
              "Перед следующим показом не подсказывайте ответы. Спросите, чего не хватает для решения. "
              "Отдельно проверяйте новый текст на следующей группе."]
    return "\n".join(lines)


def interview_summary(protocol, observations):
    tasks = indexed(rows(protocol, "tasks"), "id")
    scope = observations.get("scope")
    require(scope in {"study_fixture", "consented_anonymized"}, "Scope must identify fixture or consented anonymized data.")
    expected_fields = {"participant", "task", "variant", "code"}
    groups = defaultdict(Counter)
    seen = set()
    for row in rows(observations, "observations"):
        require(set(row) == expected_fields, "Observations accept only participant, task, variant and code.")
        participant, key, variant, code = (row[k] for k in ("participant", "task", "variant", "code"))
        require(isinstance(participant, str) and participant.startswith("P") and participant[1:].isascii()
                and participant[1:].isdigit(), "Participant must be an anonymous P-number.")
        require(key in tasks and code in {"pass", "unclear", "critical"}
                and isinstance(variant, str) and variant.strip(), "Unknown task, variant or code.")
        record = (participant, key, variant)
        require(record not in seen, f"Repeated observation: {record}")
        seen.add(record)
        groups[(key, variant)][code] += 1
    summary = [{"task": key, "role": tasks[key]["role"], "variant": variant,
                "n": sum(counts.values()), **{code: counts[code] for code in ("pass", "unclear", "critical")}}
               for (key, variant), counts in sorted(groups.items())]
    return {"scope": scope, "participants": len({record[0] for record in seen}), "groups": summary,
            "critical_tasks": sorted({row["task"] for row in summary if row["critical"]}),
            "interpretation": "Descriptive codes only; small or synthetic samples do not establish an effect."}


def event_audit(payload):
    steps = indexed(rows(payload, "steps"), "id")
    fields = ("event", "trigger", "unit", "join_key", "owner")
    for key, row in steps.items():
        require(all(isinstance(row.get(field), str) and row[field].strip() for field in fields),
                f"Step {key} needs event, trigger, unit, join_key and owner.")
    measurements = indexed(rows(payload, "metrics"), "id")
    issues = []
    by_event = defaultdict(list)
    for key, row in steps.items():
        by_event[row["event"]].append(key)
    for event, keys in by_event.items():
        if len(keys) > 1:
            issues.append({"kind": "merged_event", "event": event, "steps": keys})
    for key, metric in measurements.items():
        first, last = metric["denominator_step"], metric["numerator_step"]
        require(first in steps and last in steps and first != last, f"Invalid steps for metric {key}.")
        days = metric.get("window_days")
        require(type(days) is int and days > 0, f"Metric {key} requires a positive window_days.")
        start, end = steps[first], steps[last]
        if (start["unit"], start["join_key"]) != (end["unit"], end["join_key"]):
            issues.append({"kind": "incompatible_units", "metric": key, "steps": [first, last]})
    return {"status": "review" if issues else "schema_ready",
            "steps": list(steps), "metrics": list(measurements), "issues": issues,
            "limit": "Specification only; sequence, deduplication, coverage and tracking delivery still need QA."}
