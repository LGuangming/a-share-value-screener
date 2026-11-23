"""Regression checks for the sequential, skip-verified history archive."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from value_screener.history_batch_archive import batch_archive


class HistoricalArchiveBatchTests(unittest.TestCase):
    def test_requires_completed_descending_years(self):
        with self.assertRaises(ValueError):
            batch_archive(2020, 2025)
        with self.assertRaises(ValueError):
            batch_archive(2026, 2020)
        with self.assertRaises(ValueError):
            batch_archive(2025, 2009)

    def test_existing_verified_years_skip_tushare_and_preserve_order(self):
        with tempfile.TemporaryDirectory() as folder:
            original = os.getcwd()
            os.chdir(folder)
            try:
                with (
                    patch("value_screener.history_batch_archive.StorageConfig") as config,
                    patch("value_screener.history_batch_archive.s3_client") as s3,
                    patch("value_screener.history_batch_archive.preflight") as preflight,
                    patch("value_screener.history_batch_archive._remote_manifest") as remote,
                    patch("value_screener.history_batch_archive.verify_archive") as verify,
                    patch("value_screener.history_batch_archive.TushareClient") as tushare,
                    patch("value_screener.history_batch_archive.export_year") as export,
                ):
                    config.from_environment.return_value.bucket = "private-test"
                    remote.side_effect = lambda client, bucket, year: (
                        {"year": year}, b'{"already_verified":true}'
                    )
                    verify.side_effect = lambda client, bucket, year: {
                        "year": year, "rows": 100
                    }
                    batch_archive(2025, 2023)
                    years = [args.args[2] for args in remote.call_args_list]
                    self.assertEqual(years, [2025, 2024, 2023])
                    self.assertEqual(verify.call_count, 3)
                    tushare.from_environment.assert_not_called()
                    export.assert_not_called()
                    preflight.assert_called_once()
                    for year in years:
                        self.assertTrue(
                            (Path("artifacts/manifests") / f"{year}.json").is_file()
                        )
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
