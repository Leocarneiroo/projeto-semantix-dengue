import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests

from src import analyze


class SourceMetadataTests(unittest.TestCase):
    def test_manifest_preserves_download_date_for_unchanged_cached_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "DENGBR24.csv.zip"
            archive.write_bytes(b"archive")
            manifest = root / "source_metadata.json"
            manifest.write_text(json.dumps({
                "sources": [{"year": 2024, "sha256": analyze.file_sha256(archive), "downloaded_at": "2026-10-10T12:00:00Z"}]
            }))

            result = analyze.write_source_metadata(
                {2024: archive}, {}, manifest, run_at="2026-10-19T12:00:00Z"
            )

            self.assertEqual(result["pipeline_run_at"], "2026-10-19T12:00:00Z")
            self.assertEqual(result["sources"][0]["downloaded_at"], "2026-10-10T12:00:00Z")

    def test_unknown_download_date_is_not_filled_with_pipeline_run_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "DENGBR24.csv.zip"
            archive.write_bytes(b"archive")

            result = analyze.write_source_metadata(
                {2024: archive}, {}, root / "source_metadata.json", run_at="2026-10-19T12:00:00Z"
            )

            self.assertIsNone(result["sources"][0]["downloaded_at"])


class FetchTests(unittest.TestCase):
    def test_fetch_retries_transient_request_errors_then_replaces_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            path.write_bytes(b"old")
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.iter_content.return_value = [b"new"]
            attempts = 0

            def get(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise requests.ConnectionError("temporary")
                return response

            with patch.object(analyze.requests, "get", side_effect=get), patch.object(analyze.time, "sleep"):
                analyze.fetch("https://example.test/archive", path)

            self.assertEqual(attempts, 3)
            self.assertEqual(path.read_bytes(), b"new")
            self.assertFalse(path.with_suffix(".zip.part").exists())

    def test_fetch_keeps_existing_file_when_all_requests_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            path.write_bytes(b"old")
            with patch.object(analyze.requests, "get", side_effect=requests.ConnectionError("offline")), patch.object(analyze.time, "sleep"):
                with self.assertRaises(requests.ConnectionError):
                    analyze.fetch("https://example.test/archive", path)

            self.assertEqual(path.read_bytes(), b"old")
            self.assertFalse(path.with_suffix(".zip.part").exists())

    def test_fetch_does_not_retry_permanent_http_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            response = unittest.mock.MagicMock()
            response.raise_for_status.side_effect = requests.HTTPError(
                "not found", response=unittest.mock.Mock(status_code=404)
            )
            response.__enter__.return_value = response
            with patch.object(analyze.requests, "get", return_value=response) as get, patch.object(analyze.time, "sleep") as sleep:
                with self.assertRaises(requests.HTTPError):
                    analyze.fetch("https://example.test/missing", path)

            get.assert_called_once()
            sleep.assert_not_called()


class AtomicCacheTests(unittest.TestCase):
    def test_atomic_csv_write_keeps_previous_cache_on_write_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.csv"
            path.write_text("old\n")
            frame = pd.DataFrame({"value": [1]})
            with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    analyze.write_csv_atomic(frame, path)

            self.assertEqual(path.read_text(), "old\n")
            self.assertFalse(path.with_suffix(".csv.part").exists())


class PipelineSeparationTests(unittest.TestCase):
    def test_feature_panel_builds_next_week_target_and_lag_features(self):
        weeks = pd.date_range("2014-12-28", "2016-01-03", freq="W-SUN")
        cases = [0] * len(weeks)
        cases[weeks.get_loc(pd.Timestamp("2015-12-20"))] = 2
        cases[weeks.get_loc(pd.Timestamp("2015-12-27"))] = 3
        cases[weeks.get_loc(pd.Timestamp("2016-01-03"))] = 5
        counts = pd.DataFrame({
            "municipality_code": ["110001" for _ in weeks],
            "week_start": weeks,
            "onset_year": [week.year for week in weeks],
            "cases": cases,
        })
        reference = pd.DataFrame({
            "municipality_code": ["110001"],
            "municipality": ["Teste"],
            "uf": ["RO"],
            "population_2022": [1000],
        })

        panel, features = analyze.build_feature_panel(counts, reference)

        row = panel.loc[panel["week_start"].eq(pd.Timestamp("2015-12-27"))].iloc[0]
        self.assertEqual(row["target"], 5)
        self.assertEqual(row["lag_0"], 3)
        self.assertIn("municipality_code", features)
        self.assertIn("week_sin", features)


if __name__ == "__main__":
    unittest.main()
