"""Portable decision ledger for an IDE-hosted product strategy agent."""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from discovery import audit_copy, event_audit, interview_guide, interview_summary
from decision_map import render_brief, render_diff, render_map
from pilot import assess, economics, plan, validate_rules

MAX_BYTES = 10 * 1024 * 1024
KINDS = ("public", "owned", "synthetic", "interview")
PHASES = {
    "frame": "research",
    "research": "options",
    "options": "approval",
    "revise": "approval",
}
NEXT = {
    "frame": "Read the request, set decision frame and research questions.",
    "research": "Read relevant sources and record claims, conflicts and gaps.",
    "options": "Compare options and write both strategies and validation plan.",
    "approval": "Present the trade-off. Record a decision only after human approval.",
    "followup": "Review due commitments. Import any new evidence and map its impact.",
    "revise": "Check the affected claims and commitments. Version the strategy.",
}


def require(test, message):
    if not test:
        raise ValueError(message)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        elif tag in {"h1", "h2", "h3", "p", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(self.ignored - 1, 0)

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def xml_parts(data, suffix):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if suffix == ".docx":
            names = ["word/document.xml"]
        else:
            names = [name for name in archive.namelist()
                     if re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml|xl/sharedStrings\.xml", name)]
        require(sum(archive.getinfo(name).file_size for name in names) <= MAX_BYTES,
                "Office XML exceeds size limit")
        return {name: ElementTree.fromstring(archive.read(name)) for name in names}


def extract(data, suffix):
    if suffix in {".md", ".txt", ".csv", ".tsv", ".srt", ".vtt", ".html"}:
        content = data.decode("utf-8-sig")
        if suffix == ".html":
            parser = HTMLText()
            parser.feed(content)
            return "".join(parser.parts)
        return content
    if suffix == ".docx":
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = xml_parts(data, suffix)["word/document.xml"]
        return "\n".join("".join(t.text or "" for t in p.iter(ns + "t"))
                         for p in root.iter(ns + "p"))
    if suffix == ".xlsx":
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        parts = xml_parts(data, suffix)
        strings = []
        if "xl/sharedStrings.xml" in parts:
            strings = ["".join(t.text or "" for t in node.iter(ns + "t"))
                       for node in parts.pop("xl/sharedStrings.xml")]
        lines = []
        for name, root in sorted(parts.items()):
            for row in root.iter(ns + "row"):
                cells = []
                for cell in row.iter(ns + "c"):
                    value = cell.findtext(ns + "v", "")
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    if cell.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(ns + "t"))
                    if cell.find(ns + "f") is not None:
                        value = "[formula cached] " + value
                    cells.append(f"{cell.get('r')}={value}")
                lines.append(f"{name}:{row.get('r')} " + " | ".join(cells))
        return "\n".join(lines)
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF: install pypdf or export text with page markers") from None
        reader = PdfReader(io.BytesIO(data))
        pages = [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]
        return "\n".join(f"Page {i}\n{text}" for i, text in pages if text.strip())
    raise ValueError("Unsupported format. For media, attach a timestamped transcript or annotated screenshot.")


def connect(path, create=False):
    if create:
        path.mkdir(parents=True, exist_ok=True)
    target = path / "case.sqlite3"
    require(create or target.is_file(), "Case missing. Run init or choose the correct --case.")
    db = sqlite3.connect(target, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN IMMEDIATE")
    if create:
        db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, body TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, body TEXT)")
    return db


def get(db, key, fallback=None):
    row = db.execute("SELECT body FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else fallback


def put(db, key, body):
    db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, encode(body)))


def event(db, kind, body):
    db.execute("INSERT INTO events(at,kind,body) VALUES (?,?,?)", (now(), kind, encode(body)))


def add_source(db, args):
    if args.kind == "interview":
        require(args.consent, "Confirm consent and de-identification with --consent.")
    if args.kind == "public":
        require(args.url, "Public sources require an HTTPS URL.")
    if args.url:
        url = urlsplit(args.url)
        require(url.scheme == "https" and url.hostname and not url.username and not url.password,
                "Expected an HTTPS source URL without credentials.")
    require(args.file.stat().st_size <= MAX_BYTES, "Source exceeds 10 MiB.")
    data = args.file.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    sources = get(db, "sources", {})
    for key, row in sources.items():
        if (row["sha256"], row["url"], row["kind"]) == (sha, args.url, args.kind):
            return {"source": key, "reused": True}
    content = extract(data, args.file.suffix.lower())
    require(len(content.encode("utf-8")) <= MAX_BYTES, "Extracted text exceeds 10 MiB.")
    require(content.strip(), "Empty extraction. Supply a readable export or transcript.")
    key = f"S{len(sources)+1:03}"
    sources[key] = {"sha256": sha, "file": args.file.name, "url": args.url,
                    "kind": args.kind, "title": args.title or args.file.name,
                    "received": now(), "superseded": False, "text": content}
    put(db, "sources", sources)
    event(db, "source", {"id": key, "title": sources[key]["title"]})
    if get(db, "phase") in {"options", "approval", "followup"}:
        put(db, "phase", "revise" if get(db, "decision") else "research")
    return {"source": key, "lines": len(content.splitlines())}


def add_claim(db, args):
    sources = get(db, "sources", {})
    claims = get(db, "claims", {})
    refs = []
    for ref in args.ref:
        m = re.fullmatch(r"(S[0-9]+):([1-9][0-9]*)(?:-([1-9][0-9]*))?", ref)
        require(m is not None, "Reference format: S001:3-5")
        key, start, end = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        require(key in sources and not sources[key]["superseded"], "Unknown or superseded source: " + key)
        require(start <= end <= len(sources[key]["text"].splitlines()), "Invalid source span: " + ref)
        refs.append(ref)
    if args.kind == "fact":
        require(refs and all(sources[r.split(":")[0]]["kind"] != "synthetic" for r in refs),
                "A real-world fact needs evidence outside synthetic material.")
    entry = {"kind": args.kind, "text": args.text, "refs": refs, "state": "active"}
    for key, existing in claims.items():
        if entry == existing:
            return {"claim": key, "reused": True}
    key = f"C{len(claims)+1:03}"
    claims[key] = entry
    put(db, "claims", claims)
    event(db, "claim", {"id": key, "kind": args.kind})
    return {"claim": key}


def case_status(db, condensed=False):
    sources, claims = get(db, "sources", {}), get(db, "claims", {})
    decision = get(db, "decision")
    phase = get(db, "phase")
    review = [key for key, row in claims.items() if row["state"] == "review"]
    status = {"phase": phase, "request": get(db, "request"), "next": NEXT[phase],
              "paused": get(db, "paused"), "checkpoints": get(db, "checkpoints", {}),
              "sources": len(sources), "claims": len(claims), "review_claims": review,
              "decision": decision, "pending_changes": get(db, "pending", []),
              "proposal": get(db, "proposal")}
    if decision:
        status["due"] = [row for row in decision["commitments"]
                         if row["state"] == "open" and row["due"] <= date.today().isoformat()]
    work = [json.loads(row[0]) for row in
            db.execute("SELECT body FROM events WHERE kind='work' ORDER BY id")]
    totals = {}
    for entry in work:
        if entry["cost"] is not None:
            currency = entry["currency"]
            totals[currency] = round(totals.get(currency, 0) + entry["cost"], 6)
    status["work"] = {"minutes": round(sum(entry["minutes"] for entry in work), 3),
                      "known_cost": totals,
                      "unpriced_entries": sum(entry["cost"] is None for entry in work)}
    if condensed:
        if decision:
            status["decision"] = {k: v for k, v in decision.items() if k != "strategy"}
        if status["proposal"]:
            status["proposal"] = status["proposal"]["choice"]
        return status
    status["source_index"] = {key: {k: v for k, v in row.items() if k != "text"}
                              for key, row in sources.items()}
    status["claim_index"] = claims
    status["events"] = [dict(row) | {"body": json.loads(row["body"])}
                        for row in db.execute("SELECT * FROM events ORDER BY id")]
    return status


def validate_strategy(db, path, selected):
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), "Expected a strategy JSON object.")
    options = payload.get("options", {})
    require(isinstance(options, dict) and {"keep", "close", selected} <= options.keys()
            and len(options) >= 3, "Require keep, close and a third distinct option.")
    for key, option in options.items():
        require(isinstance(option, dict) and all(isinstance(option.get(k), str) and option[k].strip()
                for k in ("title", "objection", "mechanism", "tradeoff")), "Incomplete option: " + key)
    require(payload.get("recommendation") == selected, "Selected option differs from strategy recommendation.")
    for section, fields in {
        "product": ("audience", "job", "alternatives", "direction", "refusals", "economics"),
        "communication": ("audience", "situation", "barrier", "message", "proof", "channels"),
        "validation": ("metric", "comparison", "window", "guardrails", "threshold"),
    }.items():
        body = payload.get(section, {})
        require(isinstance(body, dict) and all(isinstance(body.get(key), str) and body[key].strip()
                for key in fields), "Incomplete " + section)
    if "pilot_rules" in payload:
        validate_rules(payload["pilot_rules"])
    claims = get(db, "claims", {})
    refs = payload.get("claims", [])
    require(isinstance(refs, list) and refs and all(isinstance(key, str) and key in claims and
            claims[key]["state"] == "active" for key in refs),
            "Strategy must link active claim IDs")
    require(not any(row["state"] == "review" for row in claims.values()),
            "Resolve affected claims before proposing.")
    return payload


def impact(db, args):
    sources, claims = get(db, "sources", {}), get(db, "claims", {})
    require(args.source in sources and not sources[args.source]["superseded"], "Unknown or obsolete new source")
    keys = set(args.claims)
    if args.supersedes:
        require(args.supersedes in sources and args.supersedes != args.source, "Invalid old source")
        keys.update(k for k, row in claims.items() if any(r.split(":")[0] == args.supersedes for r in row["refs"]))
    require(all(k in claims for k in keys), "Unknown affected claim.")
    item = {"source": args.source, "supersedes": args.supersedes,
            "claims": sorted(keys), "reason": args.reason}
    pending = get(db, "pending", [])
    if item in pending:
        return item | {"reused": True}
    if args.supersedes:
        sources[args.supersedes]["superseded"] = True
        put(db, "sources", sources)
    for key in keys:
        if claims[key]["state"] == "active":
            claims[key]["state"] = "review"
    put(db, "claims", claims)
    put(db, "pending", pending + [item])
    put(db, "phase", "revise" if get(db, "decision") else "research")
    event(db, "impact", item)
    return item


def mutate(db, args):
    cmd = args.command
    if cmd == "pause":
        put(db, "paused", args.note)
        event(db, "pause", {"note": args.note})
        return {"paused": args.note}
    if cmd == "resume":
        if get(db, "paused"):
            put(db, "paused", None)
            event(db, "resume", {})
        return case_status(db, True)
    require(not get(db, "paused"), "Case paused. Use resume.")
    phase = get(db, "phase")
    if cmd == "source":
        return add_source(db, args)
    if cmd == "claim":
        return add_claim(db, args)
    if cmd == "impact":
        return impact(db, args)
    if cmd == "resolve":
        claims = get(db, "claims", {})
        require(args.claim in claims and claims[args.claim]["state"] == "review", "Claim is not in review.")
        if args.keep:
            sources = get(db, "sources", {})
            require(not any(sources[r.split(":")[0]]["superseded"] for r in claims[args.claim]["refs"]),
                    "Replace or retire a claim linked to an obsolete source.")
        claims[args.claim]["state"] = "active" if args.keep else "withdrawn"
        put(db, "claims", claims)
        event(db, "resolve", {"claim": args.claim, "kept": args.keep, "reason": args.reason})
        return {"claim": args.claim, "state": claims[args.claim]["state"]}
    if cmd == "checkpoint":
        require(phase == args.phase and phase in {"frame", "research"}, "Checkpoint does not match phase.")
        values = get(db, "checkpoints", {})
        values[phase] = args.note
        put(db, "checkpoints", values)
        put(db, "phase", PHASES[phase])
        event(db, "checkpoint", {"phase": phase, "note": args.note})
        return {"completed": phase, "next_phase": PHASES[phase]}
    if cmd == "propose":
        require(phase in {"options", "revise", "approval"}, "Complete framing and research first.")
        payload = validate_strategy(db, args.file, args.choice)
        item = {"choice": args.choice, "strategy": payload}
        if item == get(db, "proposal") and phase == "approval":
            return {"phase": phase, "choice": args.choice, "reused": True}
        put(db, "proposal", item)
        put(db, "phase", "approval")
        event(db, "proposal", {"choice": args.choice, "claims": payload["claims"]})
        return {"phase": "approval", "choice": args.choice, "next": NEXT["approval"]}
    if cmd == "decide":
        proposal = get(db, "proposal")
        previous = get(db, "decision")
        require(phase == "approval" and proposal and proposal["choice"] == args.choice,
                "Decision must match the current proposal. Propose any alternative first.")
        commitments = [dict(row) for row in previous["commitments"]
                       if row["state"] == "open"] if previous else []
        for raw in args.commitment:
            fields = raw.split("|", 4)
            require(len(fields) == 5 and all(v.strip() for v in fields), "Commitment: owner|due|metric|trigger|action")
            owner, due, metric, trigger, action = fields
            require(date.fromisoformat(due).isoformat() == due, "Use a YYYY-MM-DD due date")
            commitments.append(dict(owner=owner, due=due, metric=metric, trigger=trigger,
                                    action=action, state="open"))
        version = previous["version"] + 1 if previous else 1
        decision = {"version": version, "choice": args.choice, "owner": args.owner, "reason": args.reason,
                    "mode": "simulation" if args.simulated else "human", "commitments": commitments,
                    "strategy": proposal["strategy"], "changes": get(db, "pending", []), "at": now()}
        put(db, "decision", decision)
        put(db, "pending", [])
        put(db, "phase", "followup")
        event(db, "decision", decision)
        return {"phase": "followup", "version": version, "choice": args.choice,
                "mode": decision["mode"], "commitments": commitments}
    if cmd == "complete":
        decision = get(db, "decision")
        require(decision and 1 <= args.index <= len(decision["commitments"]), "Unknown commitment.")
        row = decision["commitments"][args.index - 1]
        require(row["state"] == "open", "Commitment already resolved.")
        row.update(state=args.state, note=args.note)
        put(db, "decision", decision)
        event(db, "commitment", {"version": decision["version"], "index": args.index, **row})
        return row
    if cmd == "log":
        require(args.minutes >= 0 and math.isfinite(args.minutes), "Invalid time")
        require(args.cost is None or (args.cost >= 0 and math.isfinite(args.cost)), "Invalid cost")
        require(args.cost is None or args.currency, "Specify currency for known cost.")
        item = {"step": args.step, "model": args.model, "tool": args.tool,
                "minutes": args.minutes, "cost": args.cost, "currency": args.currency,
                "outcome": args.outcome}
        event(db, "work", item)
        return item
    raise ValueError("Unknown command")


def analyze(path, *, as_of=None, window_days=7, lag_days=0, round_results=True):
    """Two-arm binary outcomes measured on the same eligible cohorts."""
    counts = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        require(reader.fieldnames == ["period", "arm", "eligible", "activated", "support"],
                "Expected period,arm,eligible,activated,support")
        for line, row in enumerate(reader, 2):
            require(None not in row and all(row.values()), f"Missing or extra fields on line {line}")
            period, arm = row["period"], row["arm"]
            require(date.fromisoformat(period).isoformat() == period, f"Invalid period on line {line}")
            require(arm in {"control", "test"}, f"Unknown arm on line {line}")
            n, a, s = (int(row[k]) for k in ("eligible", "activated", "support"))
            require(n > 0 and 0 <= a <= n and 0 <= s <= n, f"Invalid counts on line {line}")
            arms = counts.setdefault(period, {})
            require(arm not in arms, f"Duplicate arm/period on line {line}")
            arms[arm] = (n, a, s)
    require(counts and all(set(v) == {"control", "test"} for v in counts.values()),
            "Each period needs both arms.")
    require(window_days > 0 and lag_days >= 0, "Window must be positive and lag nonnegative.")
    periods = sorted(counts)
    readiness = {"status": "unverified", "reason": "Supply --as-of to validate cohort maturity."}
    if as_of is not None:
        try:
            ready_on = {period: date.fromisoformat(period) + timedelta(days=window_days + lag_days)
                        for period in periods}
        except OverflowError:
            raise ValueError("Observation window exceeds supported date range.") from None
        immature = [period for period in periods if ready_on[period] > as_of]
        readiness = {"status": "blocked" if immature else "ready",
                     "as_of": as_of.isoformat(), "window_days": window_days, "lag_days": lag_days,
                     "recheck_on": max(ready_on.values()).isoformat(), "immature_periods": immature}
        if immature:
            readiness["eligible_without_full_window"] = {
                arm: sum(counts[period][arm][0] for period in immature) for arm in ("control", "test")
            }
            return {"periods": periods, "readiness": readiness, "results": None,
                    "decision_action": "Keep the current decision. Re-export after recheck_on."}
    result = {}
    def pp(value):
        return round(value*100, 3) if round_results else value*100

    z = 1.95996398454
    for idx, name in [(1, "activation"), (2, "support")]:
        arms = {}
        for arm in ("control", "test"):
            n = sum(x[arm][0] for x in counts.values())
            hits = sum(x[arm][idx] for x in counts.values())
            p = hits / n
            denom = 1 + z*z/n
            center = (p + z*z/(2*n)) / denom
            half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
            arms[arm] = (p, center-half, center+half, n, hits)
        c, t = arms["control"], arms["test"]
        delta = t[0] - c[0]
        margin_low = math.hypot(t[0]-t[1], c[2]-c[0])
        margin_high = math.hypot(t[2]-t[0], c[0]-c[1])
        result[name] = dict(control={"hits": c[4], "n": c[3]}, test={"hits": t[4], "n": t[3]},
                            delta_pp=pp(delta),
                            ci95_pp=[pp(delta-margin_low), pp(delta+margin_high)])
    return {"periods": periods, "readiness": readiness, "results": result,
            "interval": "Newcombe/Wilson, independent arms"}


def assess_case(db, args):
    current = get(db, "decision")
    require(current, "An approved decision is required before assessment.")
    selected = current
    if args.version is not None and current["version"] != args.version:
        selected = next((body for row in db.execute("SELECT body FROM events WHERE kind='decision'")
                         if (body := json.loads(row[0]))["version"] == args.version), None)
        require(selected, "Unknown decision version.")
    rules = selected["strategy"].get("pilot_rules")
    require(rules is not None, "The selected decision has no approved machine-readable pilot rules.")
    validate_rules(rules)
    outcomes = analyze(args.file, as_of=args.as_of, window_days=rules["window_days"],
                       lag_days=rules["lag_days"], round_results=False)
    costs = json.loads(args.economics.read_text(encoding="utf-8")) if args.economics else None
    report = assess(outcomes, rules, costs)
    return {"assessed_version": selected["version"], "current_version": current["version"],
            "scope": "historical" if selected["version"] != current["version"] else "current",
            "decision_mode": selected["mode"], "decision_changed": False,
            "human_approval_required": True, **report}



def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, default=Path("work/default"))
    sub = p.add_subparsers(dest="command", required=True)
    def command(name, *fields):
        parser = sub.add_parser(name)
        for field in fields:
            parser.add_argument(field)
        return parser
    command("init", "request")
    command("status").add_argument("--full", action="store_true")
    command("map").description = "Render a decision map in Markdown."
    brief = command("brief")
    brief.add_argument("--map-file", help="Sibling Markdown filename with the detailed map.")
    diff = command("diff")
    diff.add_argument("--from-version", type=int)
    diff.add_argument("--to-version", type=int)
    command("resume")
    command("pause", "note")
    command("checkpoint", "phase", "note")
    source = command("source")
    source.add_argument("file", type=Path)
    source.add_argument("--kind", required=True, choices=KINDS)
    source.add_argument("--url")
    source.add_argument("--title")
    source.add_argument("--consent", action="store_true")
    view = command("show", "id")
    view.add_argument("--start", type=int, default=1)
    view.add_argument("--count", type=int, default=60)
    command("find", "query")
    claim = command("claim", "text")
    claim.add_argument("--kind", required=True, choices=("fact", "hypothesis", "forecast", "gap", "simulation"))
    claim.add_argument("--ref", action="append", default=[])
    update = command("impact", "source", "reason")
    update.add_argument("--supersedes")
    update.add_argument("--claims", nargs="*", default=[])
    command("resolve", "claim", "reason").add_argument("--keep", action="store_true")
    proposal = command("propose", "choice")
    proposal.add_argument("file", type=Path)
    decision = command("decide", "choice", "owner", "reason")
    decision.add_argument("--commitment", action="append", required=True)
    decision.add_argument("--simulated", action="store_true")
    complete = command("complete", "note")
    complete.add_argument("index", type=int)
    complete.add_argument("--state", required=True, choices=("done", "cancelled"))
    log = command("log", "step", "outcome")
    log.add_argument("--model", required=True)
    log.add_argument("--tool", required=True)
    log.add_argument("--minutes", type=float, required=True)
    log.add_argument("--cost", type=float)
    log.add_argument("--currency")
    calc = command("analyze")
    calc.add_argument("file", type=Path)
    calc.add_argument("--as-of", type=date.fromisoformat, help="Most recent complete date in the export.")
    calc.add_argument("--window-days", type=int, default=7)
    calc.add_argument("--lag-days", type=int, default=0, help="Complete calendar days of reporting lag.")
    gate = command("assess")
    gate.add_argument("file", type=Path)
    gate.add_argument("--as-of", type=date.fromisoformat, required=True)
    gate.add_argument("--version", type=int)
    gate.add_argument("--economics", type=Path)
    size = command("plan-pilot")
    for flag in ("activation-base-pct", "activation-lift-pp", "support-base-pct",
                 "support-expected-pp", "support-limit-pp"):
        size.add_argument("--" + flag, type=float, required=True)
    size.add_argument("--daily-eligible", type=int, required=True)
    size.add_argument("--window-days", type=int, default=7)
    size.add_argument("--lag-days", type=int, default=0)
    size.add_argument("--power", type=float, default=0.8)
    margin = command("economics")
    for flag in ("activation-delta-pp", "support-delta-pp", "fixed-cost", "support-cost"):
        margin.add_argument("--" + flag, type=float, required=True)
    margin.add_argument("--eligible", type=int, required=True)
    margin.add_argument("--contribution", type=float)
    for name in ("audit-copy", "interview-guide", "interview-summary", "audit-events"):
        stage = command(name)
        stage.add_argument("file", type=Path)
        if name == "interview-summary":
            stage.add_argument("observations", type=Path)
    args = p.parse_args()
    try:
        if args.command in {"audit-copy", "interview-guide", "interview-summary", "audit-events"}:
            data = json.loads(args.file.read_text(encoding="utf-8"))
            require(isinstance(data, dict), "Expected a JSON object.")
            if args.command == "audit-copy":
                output = audit_copy(data, args.file.resolve().parent)
            elif args.command == "interview-guide":
                output = interview_guide(data)
            elif args.command == "interview-summary":
                observations = json.loads(args.observations.read_text(encoding="utf-8"))
                require(isinstance(observations, dict), "Expected a JSON object of observations.")
                output = interview_summary(data, observations)
            else:
                output = event_audit(data)
        elif args.command == "analyze":
            output = analyze(args.file, as_of=args.as_of, window_days=args.window_days, lag_days=args.lag_days)
        elif args.command == "plan-pilot":
            output = plan(args.activation_base_pct, args.activation_lift_pp, args.support_base_pct,
                          args.support_expected_pp, args.support_limit_pp, args.daily_eligible,
                          args.window_days, args.lag_days, args.power)
        elif args.command == "economics":
            output = economics(args.activation_delta_pp, args.support_delta_pp, args.eligible,
                               args.fixed_cost, args.support_cost, args.contribution)
        else:
            with connect(args.case, args.command == "init") as db:
                cmd = args.command
                if cmd == "init":
                    old = get(db, "request")
                    require(old is None or old == args.request, "Case already has a different request.")
                    if old is None:
                        put(db, "request", args.request)
                        put(db, "phase", "frame")
                        event(db, "init", {"request": args.request})
                    output = case_status(db, True)
                elif cmd == "assess":
                    output = assess_case(db, args)
                elif cmd == "status":
                    output = case_status(db, not args.full)
                elif cmd == "map":
                    output = render_map(case_status(db), get(db, "sources", {}))
                elif cmd == "brief":
                    require(not args.map_file or re.fullmatch(r"[A-Za-z0-9_-]+\.md", args.map_file),
                            "--map-file must be a sibling Markdown filename.")
                    output = render_brief(case_status(db), args.map_file)
                elif cmd == "diff":
                    output = render_diff(case_status(db), args.from_version, args.to_version)
                elif cmd == "show":
                    row = get(db, "sources", {}).get(args.id)
                    require(row is not None, "Source does not exist.")
                    require(args.start > 0 and 1 <= args.count <= 200, "Invalid span (max 200 lines).")
                    lines = row["text"].splitlines()
                    selected = lines[args.start-1:args.start-1+args.count]
                    output = {k: v for k, v in row.items() if k != "text"}
                    output["lines"] = {i: line for i, line in enumerate(selected, args.start)}
                elif cmd == "find":
                    terms = args.query.casefold().split()
                    require(terms, "Enter search terms.")
                    matches = []
                    for src, row in get(db, "sources", {}).items():
                        if not row["superseded"]:
                            for idx, line in enumerate(row["text"].splitlines(), 1):
                                score = sum(term in line.casefold() for term in terms)
                                if score:
                                    matches.append((score, f"{src}:{idx}", line[:400]))
                    output = [{"ref": ref, "text": line} for _, ref, line in sorted(matches, reverse=True)[:20]]
                else:
                    output = mutate(db, args)
        print(output if args.command in {"map", "brief", "diff", "interview-guide"} else json.dumps(output, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, IndexError, TypeError, sqlite3.Error,
            zipfile.BadZipFile, ElementTree.ParseError, AttributeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
