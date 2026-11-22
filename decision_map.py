"""Render the decision ledger as a portable, read-only Markdown view."""
import html
import re
from urllib.parse import quote


CLAIM_KIND = {
    "fact": "факт", "hypothesis": "гипотеза", "forecast": "прогноз",
    "gap": "пробел", "simulation": "симуляция",
}
CLAIM_STATE = {"active": "действует", "review": "перепроверить", "withdrawn": "отозвано"}


def plain(value):
    text = " ".join(str(value).split())
    return re.sub(r"([\\`*_{}\[\]()!|~])", r"\\\1", html.escape(text, quote=False))


def render_map(status, sources):
    decision = status["decision"]
    proposal = status["proposal"]
    changes = status["pending_changes"]
    claims = status["claim_index"]
    historical = [event["body"] for event in status["events"] if event["kind"] == "decision"]
    lines = ["# Карта оснований решения", "", plain(status["request"]), "",
             f"**Фаза:** {plain(status['phase'])}"]
    if status["paused"]:
        lines += [f"**Пауза:** {plain(status['paused'])}"]
    if decision:
        strategy = decision["strategy"]
        title = strategy["options"][decision["choice"]]["title"]
        mode = "учебное согласование" if decision["mode"] == "simulation" else "согласовано"
        lines += [f"**Версия {decision['version']} · {mode}:** {plain(title)}",
                  f"**Основание решения:** {plain(decision['reason'])}"]
    proposal_is_current = proposal and status["phase"] == "approval"
    if proposal_is_current:
        lines += [f"**На согласовании:** {plain(proposal['strategy']['options'][proposal['choice']]['title'])}"]
    primary = proposal["strategy"] if proposal_is_current else decision["strategy"] if decision else None
    if primary:
        lines += ["", "## Проверка", ""]
        for label, key in (("Метрика", "metric"), ("С чем сравнить", "comparison"),
                           ("Окно", "window"), ("Порог решения", "threshold"),
                           ("Ограничение вреда", "guardrails")):
            lines.append(f"- **{label}:** {plain(primary['validation'][key])}")
    recent = changes or (decision["changes"] if decision else [])
    if recent:
        lines += ["", "## Влияние новых материалов", ""]
        for change in recent:
            scope = ", ".join(change["claims"]) or "связанные основания не указаны"
            lines.append(f"- **{change['source']} → {scope}:** {plain(change['reason'])}")
    keys = list(dict.fromkeys([*(primary["claims"] if primary else claims),
                               *(key for change in recent for key in change["claims"])]))
    if keys:
        lines += ["", "## Основания и источники", ""]
        for key in keys:
            claim = claims[key]
            lines += [f"### {key} · {CLAIM_KIND[claim['kind']]} · {CLAIM_STATE[claim['state']]}",
                      "", plain(claim["text"]), ""]
            for ref in claim["refs"]:
                source_id, span = ref.split(":", 1)
                source = sources[source_id]
                start, _, end = span.partition("-")
                excerpt = source["text"].splitlines()[int(start)-1:int(end or start)]
                locator = f"`{ref}` · {plain(source['title'])}"
                if source["url"]:
                    url = quote(source["url"], safe=":/?#[]@!$&'()*+,;=%")
                    locator += f" · <{url}>"
                if source["superseded"]:
                    locator += " · источник заменён"
                lines += [f"- {locator}"]
                lines += [f"  > {plain(text[:300])}" for text in excerpt[:5] if text.strip()]
                if len(excerpt) > 5 or any(len(text) > 300 for text in excerpt[:5]):
                    lines += ["  > Фрагмент сокращён. Полный текст доступен через show."]
            if not claim["refs"]:
                lines += ["- Прямые ссылки отсутствуют."]
            lines += [""]
    if decision and decision["commitments"]:
        lines += ["## Обязательства", ""]
        for task in decision["commitments"]:
            lines += [f"- **{plain(task['owner'])} · {task['due']} · {plain(task['state'])}.** "
                      f"{plain(task['action'])}. Метрика — {plain(task['metric'])}. "
                      f"Триггер — {plain(task['trigger'])}."]
        lines += [""]
    if historical:
        lines += ["## История решений", ""]
        for record in historical:
            title = record["strategy"]["options"][record["choice"]]["title"]
            mode = " (симуляция)" if record["mode"] == "simulation" else ""
            lines += [f"- Версия {record['version']}{mode} · **{plain(title)}**. {plain(record['reason'])}"]
    return "\n".join(lines).rstrip()


def render_brief(status, map_file):
    decision = status["decision"]
    proposal = status["proposal"] if status["phase"] == "approval" else None
    current = proposal or decision
    if not current:
        raise ValueError("A proposal or a decision is required.")
    strategy = current["strategy"]
    option = strategy["options"][current["choice"]]
    lines = ["# Карточка решения", "", plain(status["request"]), ""]
    if proposal:
        lines += ["**Статус:** ожидает согласования.", ""]
    else:
        mode = "учебное согласование" if decision["mode"] == "simulation" else "согласовано"
        lines += [f"**Статус:** версия {decision['version']}, {mode}.", ""]
    if status["phase"] == "revise":
        lines += ["**Пересмотр открыт.** Ниже показано действующее решение.", ""]
    if status["paused"]:
        lines += [f"**Пауза:** {plain(status['paused'])}", ""]
    basis = decision["reason"] if decision and not proposal else option["tradeoff"]
    lines += [
        f"**Решение:** {plain(option['title'])}. {plain(strategy['product']['direction'])}", "",
        f"**Основание:** {plain(basis)}", "",
        f"**Главное возражение:** {plain(option['objection'])}", "",
        f"**Что измеряем:** {plain(strategy['validation']['metric'])}", "",
        f"**Условие продолжения:** {plain(strategy['validation']['threshold'])}", "",
        f"**Ограничение вреда:** {plain(strategy['validation']['guardrails'])}", "",
    ]
    commitments = [task for task in (decision["commitments"] if decision else []) if task["state"] == "open"]
    if commitments:
        task = min(commitments, key=lambda task: task["due"])
        prefix = "Ближайшее действующее обязательство" if proposal else "Ближайшее обязательство"
        lines += [f"**{prefix}:** {plain(task['owner'])}, до {task['due']}. {plain(task['action'])}", ""]
    elif decision and not proposal:
        lines += [f"**Владелец решения:** {plain(decision['owner'])}", ""]
    else:
        lines += ["**Ответственный:** назначить при согласовании.", ""]
    lines += ["**Основания в журнале:** " + ", ".join(f"`{key}`" for key in strategy["claims"]) + "."]
    if map_file:
        lines += ["", f"[Расчёты, локаторы источников и история]({map_file})"]
    return "\n".join(lines)


def commitment_key(task):
    return tuple(task[key] for key in ("owner", "due", "metric", "trigger", "action"))


def describe_commitment(task):
    owner, due, _, _, action = task
    return f"{plain(owner)} · {due} · {plain(action)}"


FIELD_LABELS = {
    "audience": "аудитория", "job": "задача клиента", "alternatives": "альтернативы",
    "direction": "направление", "refusals": "что отложено", "economics": "экономика",
    "situation": "ситуация", "barrier": "барьер", "message": "сообщение",
    "proof": "подтверждение", "channels": "каналы", "metric": "метрика",
    "comparison": "с чем сравнить", "window": "окно", "threshold": "порог",
    "guardrails": "ограничение вреда",
}
TASK_STATE = {"done": "выполнено", "cancelled": "отменено"}


def render_diff(status, from_version, to_version):
    events = status["events"]
    records = {event["body"]["version"]: event for event in events if event["kind"] == "decision"}
    if not records:
        raise ValueError("At least two recorded decision versions are required.")
    target = max(records) if to_version is None else to_version
    origin = target - 1 if from_version is None else from_version
    if origin not in records or target not in records or origin >= target:
        raise ValueError("Choose two existing decision versions in chronological order.")
    old, new = records[origin]["body"], records[target]["body"]
    before, after = old["strategy"], new["strategy"]
    lines = [f"# Сравнение решений · {origin} → {target}", "",
             f"**Было:** {plain(before['options'][old['choice']]['title'])}. {plain(old['reason'])}", "",
             f"**Стало:** {plain(after['options'][new['choice']]['title'])}. {plain(new['reason'])}", "",
             "## Что изменилось", ""]
    changes = []
    for title, section in (("Продукт", "product"), ("Коммуникации", "communication"), ("Проверка", "validation")):
        for key, value in after[section].items():
            if before[section].get(key) != value:
                changes.append(f"- **{title} · {plain(FIELD_LABELS.get(key, key))}:** {plain(before[section].get(key, '—'))} → {plain(value)}")
    lines.extend(changes or ["Продукт, коммуникации и план проверки совпадают."])
    lines += ["", "## Основания пересмотра", ""]
    refs = [change for version in sorted(records) if origin < version <= target
            for change in records[version]["body"]["changes"]]
    if refs:
        for change in refs:
            source = status["source_index"][change["source"]]
            links = ", ".join(change["claims"]) or "без привязки к конкретному основанию"
            lines.append(f"- `{change['source']}` · {plain(source['title'])} → {links}. {plain(change['reason'])}")
    else:
        lines.append("В журнале версий нет связанных записей о новых материалах.")
    added = sorted(set(after["claims"]) - set(before["claims"]))
    removed = sorted(set(before["claims"]) - set(after["claims"]))
    lines += ["", f"Основания добавлены: {', '.join(added) or '—'}. "
              f"Убраны из решения: {', '.join(removed) or '—'}.", "", "## Обязательства", ""]
    previous = {commitment_key(task) for task in old["commitments"] if task["state"] == "open"}
    current = {commitment_key(task) for task in new["commitments"] if task["state"] == "open"}
    closed = {commitment_key(event["body"]): event["body"]["state"] for event in events
              if event["kind"] == "commitment" and records[origin]["id"] < event["id"] < records[target]["id"]}
    for label, keys in (("Перешло", previous & current), ("Добавлено", current - previous),
                        ("Закрыто", (previous - current) & closed.keys()),
                        ("Исчезло из версии", previous - current - closed.keys())):
        for key in sorted(keys):
            suffix = f" ({TASK_STATE[closed[key]]})" if label == "Закрыто" else ""
            lines.append(f"- **{label}:** {describe_commitment(key)}{suffix}.")
    if not (previous or current):
        lines.append("Открытых обязательств на момент утверждения версий не было.")
    return "\n".join(lines)
