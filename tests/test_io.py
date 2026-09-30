import csv
import json
import tempfile
import unittest
from pathlib import Path

from relayrank.io import load_config, write_csv, write_json
from relayrank.models import RankedVendor


class ConfigTests(unittest.TestCase):
    def test_neko_code_and_neko_api_are_distinct_vendors(self):
        config_path = Path(__file__).parents[1] / "examples" / "config.toml"
        _, _, aliases = load_config(config_path)

        self.assertEqual(aliases["nekocode"], "Neko Code")
        self.assertEqual(aliases["nekoapi"], "Neko API")
        self.assertNotEqual(aliases["nekocode"], aliases["nekoapi"])

    def test_v2_audit_labels_strength_as_nonprobabilistic_and_keeps_legacy_zeroes(self):
        result = RankedVendor(
            rank=1,
            vendor="vendor",
            score=75,
            confidence=0.8,
            source_count=2,
            effective_weight=2,
            score_stddev=20,
            disagreement_penalty=0,
            rank_best=1,
            rank_worst=2,
            raw_score_stddev=20,
            issues=("unknown_date",),
            coverage_loss_groups=("a", "b"),
        )
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "ranking.csv"
            json_path = Path(directory) / "audit.json"
            write_csv(csv_path, [result])
            write_json(json_path, [result])
            with csv_path.open(newline="", encoding="utf-8") as handle:
                csv_row = next(csv.DictReader(handle))
            json_row = json.loads(json_path.read_text(encoding="utf-8"))[0]

        self.assertEqual(csv_row["score_stddev"], "20.00")
        self.assertEqual(csv_row["disagreement_penalty"], "0.00")
        self.assertEqual(csv_row["raw_score_stddev"], "20.00")
        self.assertEqual(csv_row["coverage_bonus"], "0.00")
        self.assertEqual(csv_row["low_outlier_sources"], "")
        self.assertEqual(json_row["score_stddev"], 20)
        self.assertEqual(json_row["disagreement_penalty"], 0)
        self.assertEqual(json_row["raw_score_stddev"], 20)
        self.assertEqual(json_row["coverage_bonus"], 0)
        self.assertEqual(json_row["low_outlier_sources"], [])
        self.assertNotIn("confidence", json_row)
        self.assertNotIn("confidence", csv_row)
        self.assertFalse(json_row["evidence_strength_is_probability"])
        self.assertEqual(json_row["issues"], ["unknown_date"])
        self.assertEqual(json_row["coverage_loss_groups"], ["a", "b"])

    def test_conflicting_vendor_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"config.toml"
            path.write_text('[aliases]\n"One" = ["shared"]\n"Two" = ["SHARED"]\n')
            with self.assertRaisesRegex(ValueError, "ambiguous vendor alias"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
