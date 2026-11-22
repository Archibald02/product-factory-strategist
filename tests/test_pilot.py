import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from demo.replay import first, second
from pilot import economics, plan, validate_rules


ROOT = Path(__file__).resolve().parents[1]


class PilotTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.case = self.tmp / "case"

    def cli(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "strateg.py", "--case", str(self.case), *map(str, args)],
            cwd=ROOT, encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result.stderr if expected else json.loads(result.stdout)

    def test_assessment_is_read_only_and_uses_committed_rules(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first(self.case)
        before = self.cli("status", "--full")
        early = self.cli("assess", "demo/updates/pilot_early_synthetic.csv", "--as-of", "2026-11-09")
        self.assertEqual(early["verdict"], "insufficient")
        self.assertEqual(early["readiness"]["recheck_on"], "2026-11-10")
        report = self.cli("assess", "demo/updates/pilot_synthetic.csv", "--as-of", "2026-11-10")
        self.assertEqual(report["assessed_version"], 1)
        self.assertEqual(report["scope"], "current")
        self.assertEqual(report["verdict"], "stop")
        results = {row["metric"]: row["status"] for row in report["checks"]}
        self.assertEqual(results["activation_point"], "pass")
        self.assertEqual(results["support_ci_upper95"], "fail")
        self.assertEqual(results["net_contribution_scenario"], "unknown")
        self.assertEqual(self.cli("status", "--full"), before)
        with contextlib.redirect_stdout(io.StringIO()):
            second(self.case)
        retrospective = self.cli("assess", "demo/updates/pilot_synthetic.csv", "--as-of", "2026-11-10",
                                 "--version", "1")
        self.assertEqual(retrospective["scope"], "historical")
        self.assertEqual(retrospective["current_version"], 2)
        self.assertIn("no approved", self.cli("assess", "demo/updates/pilot_synthetic.csv",
                                             "--as-of", "2026-11-10", expected=2))

    def test_missing_economics_cannot_pass_and_exact_point_boundary(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first(self.case)
        pilot_csv = self.tmp / "matched.csv"
        pilot_csv.write_text("period,arm,eligible,activated,support\n"
                             "2026-11-02,control,10000,2000,200\n"
                             "2026-11-02,test,10000,2300,200\n", encoding="utf-8")
        args = ("assess", pilot_csv, "--as-of", "2026-11-10")
        result = self.cli(*args)
        self.assertEqual(result["checks"][0]["status"], "pass")
        self.assertEqual(result["verdict"], "insufficient")
        costs = self.tmp / "costs.json"
        costs.write_text(json.dumps({"eligible": 10000, "fixed_cost": 120000, "support_cost": 180,
                                     "contribution_per_incremental_activation": 500}), encoding="utf-8")
        result = self.cli(*args, "--economics", costs)
        self.assertEqual(result["verdict"], "review")
        self.assertTrue(result["human_approval_required"])
        self.assertFalse(result["decision_changed"])

    def test_validates_planning_inputs_and_uses_stricter_requirement(self):
        example = plan(20, 3, 2, 0, 1, 400, 7, 0, 0.8)
        self.assertEqual(example["total"], 6154)
        self.assertEqual(example["days_to_complete"], 23)
        self.assertGreater(example["support_per_arm"], example["activation_per_arm"])
        with self.assertRaisesRegex(ValueError, "below"):
            plan(20, 3, 2, 1, 1, 400, 7, 0, 0.8)
        with self.assertRaisesRegex(ValueError, "pilot_rules"):
            validate_rules({"window_days": 7})

    def test_economics_reports_break_even_and_preserves_unknowns(self):
        example = economics(4.25, 4.05, 10000, 120000, 180)
        self.assertAlmostEqual(example["break_even_contribution_per_incremental_activation"], 453.882353)
        self.assertIsNone(example["net_contribution_per_eligible"])
        self.assertTrue(economics(4.25, 4.05, 10000, 120000, 180, 500)["nonnegative_net_contribution"])
        self.assertFalse(economics(4.25, 4.05, 10000, 120000, 180, 400)["nonnegative_net_contribution"])
        self.assertIsNone(economics(0, 4.05, 10000, 120000, 180)
                          ["break_even_contribution_per_incremental_activation"])
