"""Data accuracy regressions using the delivered official EIA snapshots."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from cushing_research.data import (
    ARCHIVE_URL, SAMPLES, _cached, knowledge_cutoff, parse_archive,
    parse_inventory_csv, parse_inventory_pdf, parse_latest_inventory, parse_public_futures,
)

ROOT = Path(__file__).resolve().parents[1]


class DataTests(unittest.TestCase):
    def test_archive_dates_include_delayed_and_orphan_html_reports(self):
        reports = parse_archive((ROOT / "data/raw/eia_archive_index.html").read_bytes(), "2026-09-14")
        by_id = {r["report_id"]: r for r in reports}
        self.assertEqual(by_id["2022_06_17_data"]["release_date"], "2022-06-29")
        self.assertEqual(by_id["2022_06_29"]["release_date"], "2022-06-29")
        self.assertNotEqual(by_id["2022_06_17_data"]["week_ending"], by_id["2022_06_29"]["week_ending"])
        self.assertEqual(by_id["2016_11_30"]["week_ending"], "2016-11-25")
        self.assertEqual(by_id["2017_11_29"]["week_ending"], "2017-11-24")
        weeks = {r["week_ending"] for r in reports}
        expected = {d.date().isoformat() for d in pd.date_range("2011-07-29", "2026-09-04", freq="W-FRI")}
        self.assertEqual(weeks, expected)
        self.assertEqual(len(reports), 789)

    def test_end_of_day_cutoff_observes_dst_and_late_publication(self):
        self.assertEqual(knowledge_cutoff("2025-12-29"), "2025-12-30T04:59:59+00:00")
        self.assertEqual(knowledge_cutoff("2022-06-29"), "2022-06-30T03:59:59+00:00")
        self.assertEqual(knowledge_cutoff("2024-03-08"), "2024-03-09T04:59:59+00:00")
        self.assertEqual(knowledge_cutoff("2024-03-11"), "2024-03-12T03:59:59+00:00")

    def test_five_official_inventory_anchors_and_national_scope(self):
        for report_id, (week, _, cushing) in SAMPLES.items():
            with self.subTest(report_id=report_id):
                path = ROOT / "data/raw/inventory" / f"{report_id}_table9.csv"
                actual, national = parse_inventory_csv(path.read_bytes(), week)
                self.assertAlmostEqual(actual, cushing, places=6)
                self.assertGreater(national, actual)
        data = (ROOT / "data/raw/inventory/2020_04_22_table9.csv").read_bytes()
        self.assertAlmostEqual(parse_inventory_csv(data, "2020-04-17")[1], 518.640)

    def test_wrong_observation_date_and_missing_values_fail(self):
        data = (ROOT / "data/raw/inventory/2020_04_22_table9.csv").read_bytes()
        with self.assertRaisesRegex(ValueError, "week mismatch"):
            parse_inventory_csv(data, "2020-04-10")
        corrupted = data.replace(b'"Cushing, Oklahoma","59.741"', b'"Cushing, Oklahoma","NA"')
        self.assertNotEqual(data, corrupted)
        with self.assertRaises(ValueError):
            parse_inventory_csv(corrupted, "2020-04-17")

    def test_offline_cache_does_not_request_network_or_mutate_manifest(self):
        path = ROOT / "data/raw/eia_archive_index.html"
        manifest = path.with_suffix(path.suffix + ".source.json")
        previous = manifest.read_bytes()
        with patch("cushing_research.data.urlopen", side_effect=AssertionError("network forbidden")) as network:
            data, meta = _cached(path, ARCHIVE_URL)
            network.assert_not_called()
        self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"])
        self.assertEqual(previous, manifest.read_bytes())

    def test_checksum_mismatch_rejects_modified_snapshot(self):
        source = ROOT / "data/raw/eia_archive_index.html"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / source.name
            shutil.copy2(source, path)
            manifest = path.with_suffix(path.suffix + ".source.json")
            shutil.copy2(source.with_suffix(source.suffix + ".source.json"), manifest)
            original = manifest.read_bytes()
            path.write_bytes(path.read_bytes() + b"\nmodified")
            with patch("cushing_research.data.urlopen", side_effect=AssertionError("network forbidden")):
                with self.assertRaisesRegex(ValueError, "SHA256/URL mismatch"):
                    _cached(path, ARCHIVE_URL)
            self.assertEqual(original, manifest.read_bytes())

    def test_futures_negative_anchor_holiday_and_final_date(self):
        data = (ROOT / "data/raw/public_futures/RCLC1D.html").read_bytes()
        frame = parse_public_futures(data, "F1").set_index("trade_date")
        self.assertAlmostEqual(frame.loc["2020-04-20", "F1"], -37.63)
        self.assertAlmostEqual(frame.loc["2024-04-05", "F1"], 86.91)
        self.assertNotIn("2024-03-29", frame.index)
        self.assertEqual(frame.index.max(), "2024-04-05")
        second = parse_public_futures((ROOT / "data/raw/public_futures/RCLC2D.html").read_bytes(), "F2").set_index("trade_date")
        self.assertAlmostEqual(second.loc["2020-04-20", "F2"], 20.43)

    def test_processed_inventory_has_all_real_weeks_and_consistent_hashes(self):
        frame = pd.read_csv(ROOT / "data/processed/inventory.csv")
        audit = json.loads((ROOT / "data/processed/inventory_audit.json").read_text())
        self.assertEqual(audit["status"], "complete")
        self.assertFalse(frame.week_ending.duplicated().any())
        self.assertTrue(frame[["cushing_mbbl", "us_mbbl"]].notna().all().all())
        self.assertTrue((frame.cushing_mbbl < frame.us_mbbl).all())
        self.assertEqual(len(frame), 789)
        by_report = {s["report_id"]: s for s in audit["source_hashes"]}
        for row in frame.itertuples():
            raw = ROOT / by_report[row.report_id]["raw_file"]
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), row.sha256)
            self.assertEqual(knowledge_cutoff(row.release_date), row.knowledge_cutoff)

    def test_original_pdf_recovers_misdated_csv_at_printed_precision(self):
        rejected = (ROOT / "data/raw/inventory/2019_07_03_table9.csv").read_bytes()
        with self.assertRaisesRegex(ValueError, "week mismatch"):
            parse_inventory_csv(rejected, "2019-06-28")
        pdf = (ROOT / "data/raw/inventory/2019_07_03_table4.pdf").read_bytes()
        self.assertEqual(parse_inventory_pdf(pdf, "2019-06-28"), (52.5, 468.5))
        with self.assertRaisesRegex(ValueError, "current-week header"):
            parse_inventory_pdf(pdf, "2019-06-21")

    def test_original_mixed_rounding_does_not_discard_valid_2020_weeks(self):
        for report, week, expected in [("2020_03_18", "2020-03-13", (38.445, 453.737)), ("2020_04_15", "2020-04-10", (54.965, 503.618))]:
            self.assertEqual(parse_inventory_csv((ROOT / f"data/raw/inventory/{report}_table9.csv").read_bytes(), week), expected)

    def test_latest_history_remains_separate_and_records_real_discrepancies(self):
        frame = parse_latest_inventory((ROOT / "data/raw/inventory_latest/W_EPC0_SAX_YCUOK_MBBL.html").read_bytes(), "cushing_mbbl").set_index("week_ending")
        self.assertEqual(frame.loc["2004-04-09", "cushing_mbbl"], 11.677)
        self.assertEqual(frame.loc["2019-06-28", "cushing_mbbl"], 52.488)
        original = pd.read_csv(ROOT / "data/processed/inventory.csv").set_index("week_ending")
        self.assertEqual(original.loc["2019-06-28", "cushing_mbbl"], 52.5)
        audit = json.loads((ROOT / "data/processed/inventory_audit.json").read_text())["latest_vintage_crosscheck"]
        self.assertEqual(audit["status"], "complete")
        self.assertFalse(audit["archived_values_overwritten"])
        self.assertEqual(audit["compared_rows"], 789)
        self.assertEqual(audit["differing_values_by_series"]["cushing_mbbl"], 1)
        self.assertEqual(audit["difference_classification_counts"], {"lease_scope_change_confirmed_against_including_lease_series": 271, "within_printed_pdf_rounding": 2})
        self.assertEqual(audit["national_scope_change"]["first_observation_excluding_lease"], "2016-10-07")
        self.assertEqual(audit["national_scope_change"]["first_release_excluding_lease"], "2016-10-13")

    def test_all_processed_and_raw_snapshots_have_valid_sha256(self):
        for name in ["inventory", "public_futures"]:
            audit = json.loads((ROOT / f"data/processed/{name}_audit.json").read_text())
            data = (ROOT / f"data/processed/{name}.csv").read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), audit["processed_sha256"])
        audit = json.loads((ROOT / "data/processed/inventory_audit.json").read_text())["latest_vintage_crosscheck"]
        self.assertEqual(hashlib.sha256((ROOT / audit["processed_file"]).read_bytes()).hexdigest(), audit["processed_sha256"])
        for manifest in (ROOT / "data/raw").rglob("*.source.json"):
            raw = Path(str(manifest).removesuffix(".source.json"))
            meta = json.loads(manifest.read_text())
            with self.subTest(source=raw.name):
                self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), meta["sha256"])


if __name__ == "__main__":
    unittest.main()
