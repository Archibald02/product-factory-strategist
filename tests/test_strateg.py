import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import strateg
from demo.replay import first, second
from demo.delivery.replay import replay as delivery_replay
from evaluation.compare import evaluate

ROOT = Path(__file__).resolve().parents[1]


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name) / "case"

    def cli(self, *args, expected=0):
        output = subprocess.run(
            [sys.executable, "-X", "utf8", "strateg.py", "--case", str(self.case), *args],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        self.assertEqual(output.returncode, expected, output.stderr)
        return json.loads(output.stdout) if expected == 0 else output.stderr

    def test_end_to_end_pause_and_revision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first(self.case)
            self.assertEqual(self.cli("status")["phase"], "followup")
            self.assertIn("paused", self.cli("checkpoint", "frame", "no", expected=2))
            second(self.case)
        result = self.cli("status", "--full")
        self.assertEqual(result["decision"]["version"], 2)
        self.assertEqual(result["decision"]["choice"], "keep")
        self.assertEqual(result["claim_index"]["C007"]["state"], "withdrawn")
        self.assertEqual(result["claim_index"]["C009"]["kind"], "simulation")
        self.assertEqual(sum(e["kind"] == "source" for e in result["events"]), 7)
        self.assertEqual([e["body"]["choice"] for e in result["events"] if e["kind"] == "decision"],
                         ["prototype", "keep"])

    def test_decision_map_tracks_history_and_pending_changes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first(self.case)
            second(self.case)
        command = [sys.executable, "-X", "utf8", "strateg.py", "--case", str(self.case), "map"]
        report = subprocess.check_output(command, cwd=ROOT, text=True, encoding="utf-8")
        for fragment in ("Версия 2", "Версия 1", "C007 · прогноз · отозвано", "S007:2-5",
                         "Продукт и поддержка", "https://support.mts.ru/"):
            self.assertIn(fragment, report)

    def test_delivery_retires_incomparable_evidence(self):
        with contextlib.redirect_stdout(io.StringIO()) as trace:
            delivery_replay(self.case)
        self.assertIn("unavailable-discount-economics.csv", trace.getvalue())
        status = self.cli("status", "--full")
        self.assertEqual(status["decision"]["choice"], "keep")
        self.assertEqual(status["claim_index"]["C001"]["state"], "withdrawn")
        self.assertEqual(status["claim_index"]["C002"]["state"], "withdrawn")
        self.assertIn("24%", status["claim_index"]["C004"]["text"])

    def test_comparison_does_not_impute_missing_observations(self):
        sample = dict(seconds=None, human_edits=0, verified_claims=2, total_claims=4,
                      criteria=[1, 1, None, 0])
        result = evaluate({"chat": sample, "agent": sample | {"verified_claims": 3}})
        self.assertEqual(result["agent_minus_chat"]["verified_fraction"], 0.25)
        self.assertIsNone(result["agent_minus_chat"]["seconds"])
        self.assertIsNone(result["agent"]["criteria_passed"])

    def test_unrelated_case_source_replacement_and_provenance(self):
        self.cli("init", "Проверить загрузку слотов музыкальной школы")
        self.cli("checkpoint", "frame", "Решить, стоит ли добавлять вечернюю группу.")
        file = Path(self.tmp.name) / "schedule.html"
        file.write_text("<script>drop()</script><p>Вторник 18:00 — 8 учебных мест.</p>", encoding="utf-8")
        self.assertIn("HTTPS URL", self.cli("source", str(file), "--kind", "public", expected=2))
        self.cli("source", str(file), "--kind", "owned")
        self.assertNotIn("drop()", str(self.cli("show", "S001")))
        self.cli("claim", "8 учебных мест", "--kind", "fact", "--ref", "S001:2")
        file.write_text("<p>Вторник 18:00 — 9 учебных мест.</p>", encoding="utf-8")
        self.cli("source", str(file), "--kind", "owned")
        self.assertEqual(self.cli("impact", "S002", "Исправленная версия", "--supersedes", "S001")["claims"],
                         ["C001"])
        self.assertIn("obsolete source", self.cli("resolve", "C001", "keep", "--keep", expected=2))
        self.cli("resolve", "C001", "Устарел")
        self.cli("claim", "9 учебных мест", "--kind", "fact", "--ref", "S002:2")
        self.assertEqual(self.cli("find", "мест")[0]["ref"], "S002:2")

    def test_no_fact_from_model_interview_and_no_premature_approval(self):
        self.cli("init", "Новая задача")
        self.cli("source", "demo/input/model_interview.txt", "--kind", "synthetic")
        self.assertIn("outside synthetic", self.cli("claim", "реальный спрос", "--kind", "fact",
                                                   "--ref", "S001:4", expected=2))
        self.assertIn("current proposal", self.cli("decide", "keep", "PM", "yes", "--commitment",
                                                  "PM|2026-12-03|X|Y|Z", expected=2))

    def test_portable_document_extractors(self):
        import zipfile
        word = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Brief</w:t></w:r></w:p></w:document>'
        sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>11</v></c></row></sheetData></worksheet>'
        for suffix, part, xml, expected in [
            (".docx", "word/document.xml", word, "Brief"),
            (".xlsx", "xl/worksheets/sheet1.xml", sheet, "A1=11"),
        ]:
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w") as archive:
                archive.writestr(part, xml)
            self.assertIn(expected, strateg.extract(memory.getvalue(), suffix))

    def test_rate_check_and_missing_arm(self):
        output = strateg.analyze(ROOT / "demo/updates/pilot_synthetic.csv")
        self.assertEqual(output["results"]["activation"]["delta_pp"], 4.25)
        self.assertEqual(output["results"]["support"]["delta_pp"], 4.05)
        file = Path(self.tmp.name) / "missing.csv"
        file.write_text("period,arm,eligible,activated,support\n2026-11-03,control,10,1,0\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "both arms"):
            strateg.analyze(file)


if __name__ == "__main__":
    unittest.main()
