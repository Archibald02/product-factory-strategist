import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from discovery import audit_copy, event_audit, interview_summary

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo/discovery"


def load(name):
    return json.loads((DEMO / name).read_text(encoding="utf-8"))


class DiscoveryTest(unittest.TestCase):
    def test_audit_matches_other_screen_and_rejects_changed_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "screen.txt").write_text("Your library card works everywhere.\n", encoding="utf-8")
            (path / "terms.txt").write_text("Only regional branches are covered.\n", encoding="utf-8")
            row = {"id": "L1", "screen": "Library app", "phrase": "Your library card works everywhere.",
                   "copy_ref": "screen.txt:1", "evidence": ["terms.txt:1"], "judgment": "contradicted",
                   "reason": "Scope is narrower.", "proposed_copy": "Use at regional branches."}
            payload = {"scope": "project", "claims": [row]}
            self.assertEqual(audit_copy(payload, path)["requires_review"], ["L1"])
            (path / "screen.txt").write_text("Your library card works regionally.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs"):
                audit_copy(payload, path)

    def test_interview_requires_codes_and_disallows_duplicate(self):
        protocol, observations = load("interview.json"), load("observations_synthetic.json")
        summary = interview_summary(protocol, observations)
        self.assertEqual(summary["scope"], "study_fixture")
        self.assertEqual(summary["participants"], 4)
        self.assertEqual(summary["critical_tasks"], ["accept", "cost", "eligible"])
        with self.assertRaisesRegex(ValueError, "Repeated"):
            interview_summary(protocol, observations | {
                "observations": observations["observations"] + [observations["observations"][0]]
            })
        with self.assertRaisesRegex(ValueError, "only participant"):
            interview_summary(protocol, observations | {
                "observations": [observations["observations"][0] | {"contact": "example"}]
            })

    def test_event_map_preserves_distinct_outcomes_and_units(self):
        bad = event_audit(load("events_draft.json"))
        self.assertEqual(bad["status"], "review")
        self.assertEqual({issue["kind"] for issue in bad["issues"]},
                         {"merged_event", "incompatible_units"})
        good = event_audit(load("events_corrected.json"))
        self.assertEqual(good["status"], "schema_ready")
        self.assertEqual(good["issues"], [])
        with self.assertRaisesRegex(ValueError, "Invalid steps"):
            event_audit({"steps": load("events_corrected.json")["steps"],
                         "metrics": [{"id": "new", "denominator_step": "sent",
                                      "numerator_step": "unknown", "window_days": 30}]})

    def test_cli_writes_structured_audit_and_markdown_guide(self):
        cmd = [sys.executable, "-X", "utf8", "strateg.py"]
        audit = subprocess.check_output([*cmd, "audit-copy", str(DEMO / "audit.json")],
                                        cwd=ROOT, encoding="utf-8")
        self.assertEqual(json.loads(audit)["requires_review"], ["C01", "C02", "C03"])
        guide = subprocess.check_output([*cmd, "interview-guide", str(DEMO / "interview.json")],
                                        cwd=ROOT, encoding="utf-8")
        self.assertIn("Критическая ошибка", guide)
        self.assertIn("согласие", guide)
