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
