"""Recorded strategy decisions replayed against a fresh local case. No LLM calls."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(case, *args, expected=0, summary=True):
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "strateg.py", "--case", str(case), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != expected:
        raise RuntimeError(f"{args[0]} returned {result.returncode}: {result.stderr}")
    if summary:
        print("› " + args[0] + (" " + args[1] if len(args) > 1 and args[0] in
              {"checkpoint", "show", "propose", "decide", "source", "impact", "resolve"} else ""))
        if result.stdout:
            payload = json.loads(result.stdout)
            if isinstance(payload, dict) and "request" in payload:
                payload = {k: payload[k] for k in ("phase", "paused", "next", "sources", "claims")}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(result.stderr.strip())
    return time.perf_counter() - started


def first(case):
    if (case / "case.sqlite3").exists():
        raise ValueError("Use a fresh path for first. Use second to resume a paused demo.")
    def invoke(*args, **kwargs):
        return run(case, *args, **kwargs)
    started = time.perf_counter()
    invoke("init", "МегаСемья: как приглашать близких так, чтобы подключалось больше семей?")
    invoke("source", "demo/input/request.md", "--kind", "owned")
    invoke("checkpoint", "frame", "Решить, нужен ли один спринт. Цель — D7 подключения при допустимой поддержке. "
           "Проверить применимость, конкурентные механики и альтернативные причины отказа.")
    webpages = [
        ("megafon_cnews.txt", "https://www.cnews.ru/news/line/2025-09-18_megafon_stiraet_granitsy"),
        ("megafon_shop.txt", "https://shop.megafon.ru/connect/tariffs/508722"),
        ("mts.txt", "https://support.mts.ru/mts_mobilnaya_svyaz/gruppi-dlya-blizkih/usluga-obschii-paket"),
        ("beeline.txt", "https://kola.beeline.ru/customers/support/sim-i-nomer/prozedury/semya-v-beeline/"),
    ]
    for filename, url in webpages:
        invoke("source", "demo/input/" + filename, "--kind", "public", "--url", url)
    invoke("source", "demo/input/model_interview.txt", "--kind", "synthetic")
    claims = [
        ("fact", "В 2025 МегаФон объявил о межрегиональной МегаСемье.", "S002:2-3"),
        ("fact", "Индекс карточки Семейный + упоминает пакет. Полная страница недоступна.", "S003:2-3"),
        ("fact", "Общий пакет МТС из справки ограничен регионом и перечнем тарифов.", "S004:2-3"),
        ("fact", "Справка билайна по Коле описывает региональное условие и общий пакет.", "S005:2-3"),
        ("hypothesis", "Принятию мешают непонятный отправитель или условия оплаты.", "S006:3-5"),
        ("gap", "Нет реальной воронки, актуальных условий на конкретную пару и затрат.", "S001:4-5"),
        ("forecast", "В сценарии проверки нагрузка может остаться в пределах +1 п.п.", "S001:3-5"),
    ]
    for kind, statement, ref in claims:
        invoke("claim", statement, "--kind", kind, "--ref", ref)
    invoke("checkpoint", "research", "Открытые источники подтверждают различие механик в сравниваемых примерах. "
           "К причинам отказа и актуальным тарифам применима низкая уверенность.")
    invoke("propose", "prototype", "demo/results/v1.json")
    invoke("decide", "prototype", "Руководитель продукта (симуляция)",
           "Проверить сценарий в одном сегменте с порогами активации и вреда.",
           "--simulated", "--commitment",
           "Аналитик|2026-11-22|D7 и тематическая поддержка|рост поддержки выше +1 п.п.|остановить расширение")
    invoke("log", "replay-v1", "Автоматическое воспроизведение из фиксированных файлов", "--model", "none",
           "--tool", "Python CLI", "--minutes", str((time.perf_counter()-started)/60), "--cost", "0", "--currency", "RUB",
           summary=False)
    invoke("pause", "Перерыв после моделируемого решения. При продолжении проверить поступивший CSV.")


def second(case):
    def invoke(*args, **kwargs):
        return run(case, *args, **kwargs)
    started = time.perf_counter()
    invoke("status")
    invoke("resume")
    invoke("source", "demo/updates/pilot_synthetic.csv", "--kind", "synthetic")
    invoke("source", "demo/updates/pilot_synthetic.csv", "--kind", "synthetic")
    invoke("analyze", "demo/updates/pilot_synthetic.csv")
    invoke("impact", "S007", "Тематические обращения превысили защитный порог", "--claims", "C007")
    invoke("resolve", "C007", "В учебных данных рост +4,05 п.п. выше порога +1 п.п.")
    invoke("claim", "Учебная разность активаций +4,25 п.п.", "--kind", "simulation", "--ref", "S007:2-5")
    invoke("claim", "Учебная разность обращений +4,05 п.п.", "--kind", "simulation", "--ref", "S007:2-5")
    invoke("complete", "Порог превышен в учебной таблице. Расширение в симуляции остановлено.",
           "1", "--state", "done")
    invoke("propose", "keep", "demo/results/v2.json")
    invoke("decide", "keep", "Руководитель продукта (симуляция)", "Защитный порог превышен.", "--simulated",
           "--commitment", "Продукт и поддержка|2026-12-03|Причины обращений D7|повторяемая причина|исправить и перепроверить")
    invoke("log", "replay-v2", "Пересмотр по синтетической таблице", "--model", "none",
           "--tool", "Python CLI", "--minutes", str((time.perf_counter()-started)/60), "--cost", "0", "--currency", "RUB",
           summary=False)
    invoke("status")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("first", "second"))
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    {"first": first, "second": second}[args.stage](args.case.resolve())
