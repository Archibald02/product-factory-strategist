"""Portable smoke scenario for a changed metric definition and a missing file."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def replay(case):
    def cli(*args, expected=0):
        result = subprocess.run([sys.executable, "-X", "utf8", "strateg.py", "--case", str(case), *map(str, args)],
                                cwd=ROOT, text=True, encoding="utf-8", capture_output=True)
        if result.returncode != expected:
            raise RuntimeError(result.stderr or result.stdout)
        print(f"› {args[0]}\n{(result.stdout or result.stderr).strip()}")
        return json.loads(result.stdout) if not expected else None

    cli("init", "Нужен ли промокод на второй заказ в Листке?")
    cli("checkpoint", "frame", "Один спринт. D30 оплаченных повторов, маржа и возвраты.")
    cli("source", HERE / "inputs/dashboard.csv", "--kind", "synthetic")
    cli("source", HERE / "inputs/metric-v1.md", "--kind", "synthetic")
    cli("source", HERE / "inputs/unavailable-discount-economics.csv", "--kind", "synthetic", expected=2)
    cli("log", "research", "Экономика скидок недоступна. Продолжена сверка определения D30.",
        "--model", "none", "--tool", "Python CLI", "--minutes", "0", "--cost", "0", "--currency", "RUB")
    cli("claim", "Дашборд показывает 32% и 24%, правила расчёта обеих когорт ещё не сверены.",
        "--kind", "simulation", "--ref", "S001:2-3")
    cli("claim", "По доступной версии справочника показатель использует событие создания.",
        "--kind", "simulation", "--ref", "S002:2-3")
    cli("pause", "Ждём актуальное определение метрики и пересчёт.")
    cli("resume")
    cli("source", HERE / "update/metric-v2.md", "--kind", "synthetic")
    cli("source", HERE / "update/comparable.csv", "--kind", "synthetic")
    cli("impact", "S003", "Начиная с июня берутся оплаченные, не отменённые заказы.",
        "--supersedes", "S002", "--claims", "C001")
    cli("resolve", "C001", "Сырые показания нельзя использовать как сравнимую метрику.")
    cli("resolve", "C002", "Определение уточнено по версии 2.")
    cli("claim", "Июнь использует другое событие; старый ряд сравнивать с новым нельзя.",
        "--kind", "simulation", "--ref", "S003:2-3")
    cli("claim", "По одному определению 480/2000 = 24% в обеих когортах.",
        "--kind", "simulation", "--ref", "S004:2-3")
    cli("checkpoint", "research", "Сопоставимое падение не подтверждается. Нет экономики скидок.")
    cli("propose", "keep", HERE / "results/v1.json")
    cli("decide", "keep", "Руководитель продукта (симуляция)", "Показанная разность вызвана сменой определения.",
        "--simulated", "--commitment",
        "Аналитик|2026-12-03|Сравнимый D30|Ряд пересчитан|Проверить сегменты на устойчивое падение")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python demo/delivery/replay.py work/delivery")
    case = Path(sys.argv[1]).resolve()
    if (case / "case.sqlite3").exists():
        sys.exit("Use a fresh case for this replay.")
    replay(case)
