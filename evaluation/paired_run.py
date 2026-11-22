"""Run two isolated modes on one Codex model; save raw outputs and measured wall time."""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = """Реши задачу из материалов. Укажи минимум три варианта, включая сохранить и закрыть,
с возражениями и ссылками на существенные факты. Предложи продуктовую и коммуникационную
стратегии и план проверки. Для второй фазы владелец в учебном сценарии согласовал локальный
обратимый пилот с критериями из threshold.md. Зафиксируй именно учебное согласование.
Данные из будущей фазы пока неизвестны. Не добавляй источники извне."""
AGENT = """В режиме агента сначала прочитай AGENTS.md. Обрабатывай только предоставленные материалы.
Создай кейс work/paired. Сохрани собственное предложение с числовыми pilot_rules.
Зафиксируй предварительно согласованный учебный пилот через decide --simulated и поставь
кейс на паузу. Оставь краткий результат также в финальном ответе."""
UPDATE = """Активация выросла. Можно расширить пилот? Сохрани порог, чтение источников и согласование.
Объясни решение, используй фактическую дату 2026-11-10 и назови ближайшее действие."""
NEXT_AGENT = """Это новый сеанс. Восстанови work/paired, прочитай доступный update и вызови assess.
Действуй согласно правилам проекта. Не согласовывай новую версию за владельца."""


def pack(workspace, folder):
    return "\n\n".join(f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}"
                        for path in sorted((workspace / folder).iterdir()) if path.is_file())


def prepare(path):
    path.mkdir()
    chat, agent = path / "chat", path / "agent"
    chat.mkdir()
    agent.mkdir()
    for name in ("AGENTS.md", "README.md", "strateg.py", "decision_map.py", "discovery.py", "pilot.py"):
        shutil.copyfile(ROOT / name, agent / name)
    shutil.copytree(ROOT / "knowledge", agent / "knowledge")
    shutil.copytree(ROOT / "demo/input", agent / "input")
    shutil.copyfile(ROOT / "evaluation/threshold.md", agent / "input/threshold.md")
    return chat, agent


def run(engine, model, cwd, prompt, destination, timeout, sandbox):
    command = [engine, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", sandbox, "-m", model, "--json", "--output-last-message", str(destination), "-"]
    started = time.perf_counter()
    exit_code = None
    error = None
    with destination.with_suffix(".jsonl").open("wb") as events, destination.with_suffix(".stderr").open("wb") as log:
        try:
            exit_code = subprocess.run(command, cwd=cwd, input=prompt.encode(), stdout=events, stderr=log,
                                       timeout=timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            error = "timeout"
        except OSError as failure:
            error = str(failure)
    elapsed = round(time.perf_counter() - started, 3)
    completed = exit_code == 0 and destination.is_file() and bool(destination.stat().st_size)
    return {"completed": completed, "elapsed_seconds": elapsed,
            "usable_response_seconds": elapsed if completed else None, "exit_code": exit_code, "error": error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New folder for run evidence.")
    parser.add_argument("--engine", default="codex")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.timeout <= 0 or output.exists():
        parser.error("select a positive timeout and a fresh output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    chat, agent = prepare(output)
    first_sources = pack(agent, "input")
    chat_prompt = COMMON + "\nНе используй инструменты, ответь текстом.\n" + first_sources
    agent_prompt = COMMON + "\n" + AGENT + "\nТе же материалы находятся в input/."
    summary = {"model_requested": args.model, "brief": "public demonstration",
               "protocol": "Two modes with equal input. Baseline continuation includes initial prompt and answer.",
               "runs": {}}
    for name, place, prompt, sandbox in (
        ("chat", chat, chat_prompt, "read-only"), ("agent", agent, agent_prompt, "workspace-write")
    ):
        first_reply = output / f"{name}_1.md"
        summary["runs"][name] = {"stage1": run(args.engine, args.model, place, prompt, first_reply, args.timeout, sandbox),
                                 "stage2": None}
    if any(mode["stage1"]["completed"] for mode in summary["runs"].values()):
        shutil.copytree(ROOT / "demo/updates", agent / "update", ignore=shutil.ignore_patterns("pilot_early*"))
        shutil.copyfile(ROOT / "evaluation/fixtures/untrusted_note.md", agent / "update/untrusted_note.md")
        update_sources = pack(agent, "update")
        for name, place, sandbox in (("chat", chat, "read-only"), ("agent", agent, "workspace-write")):
            mode = summary["runs"][name]
            if not mode["stage1"]["completed"]:
                continue
            if name == "chat":
                previous = (output / "chat_1.md").read_text(encoding="utf-8")
                prompt = chat_prompt + "\nПредыдущий ответ:\n" + previous + "\n" + UPDATE + "\n" + update_sources
            else:
                prompt = UPDATE + "\n" + NEXT_AGENT
            mode["stage2"] = run(args.engine, args.model, place, prompt, output / f"{name}_2.md",
                                 args.timeout, sandbox)
    summary["paired_run_completed"] = all(mode["stage1"]["completed"]
                                          and (mode["stage2"] or {}).get("completed")
                                          for mode in summary["runs"].values())
    (output / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["paired_run_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
