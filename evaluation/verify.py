"""Run the full offline verification with disposable case databases."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def call(*args, capture=False):
    return subprocess.run([sys.executable, "-X", "utf8", *map(str, args)],
                          cwd=ROOT, check=True, text=True, encoding="utf-8",
                          capture_output=capture).stdout


def assert_value(condition, explanation):
    if not condition:
        raise RuntimeError(explanation)


def main():
    call("-m", "unittest", "discover", "-s", "tests", "-v")
    with tempfile.TemporaryDirectory(prefix="strateg-verify-") as tmp:
        megafon = Path(tmp) / "megafon"
        delivery = Path(tmp) / "delivery"

        def case_cmd(case, *args):
            return json.loads(call("strateg.py", "--case", case, *args, capture=True))

        call("demo/replay.py", "first", "--case", megafon, capture=True)
        early = case_cmd(megafon, "assess", "demo/updates/pilot_early_synthetic.csv",
                         "--as-of", "2026-11-09")
        assert_value(early["verdict"] == "insufficient", "Early export passed the maturity gate")
        call("demo/replay.py", "second", "--case", megafon, capture=True)
        latest = case_cmd(megafon, "status")
        assert_value(latest["decision"]["version"] == 2 and latest["decision"]["choice"] == "keep",
                     "Unexpected Megafon decision")
        gate = case_cmd(megafon, "assess", "demo/updates/pilot_synthetic.csv", "--as-of", "2026-11-10",
                        "--version", "1")
        assert_value(gate["verdict"] == "stop", "The support guardrail did not stop the pilot")

        call("demo/delivery/replay.py", delivery, capture=True)
        second = case_cmd(delivery, "status", "--full")
        assert_value(second["decision"]["choice"] == "keep"
                     and second["claim_index"]["C001"]["state"] == "withdrawn"
                     and "24%" in second["claim_index"]["C004"]["text"],
                     "Delivery metric revisions were not preserved")
    print("PASS: both demos ran from empty directories; Megafon stop and delivery reconciliation verified.")


if __name__ == "__main__":
    main()
