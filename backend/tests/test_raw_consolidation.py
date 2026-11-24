import unittest
from value_screener.raw_consolidation import SOURCE, DEST, destination, translate_manifest, references, required_keys


class RawConsolidationTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(destination(SOURCE + "prices/year=2020/month=01.parquet"),
                         DEST + "daily_prices/year=2020/month=01.parquet")
        self.assertEqual(destination(SOURCE + "phase2/financial_snapshot/income/x.jsonl.gz"),
                         DEST + "financial/income/x.jsonl.gz")
        with self.assertRaises(RuntimeError):
            destination(SOURCE + "other/x")

    def test_manifest_paths_only(self):
        key = SOURCE + "phase2/daily_basic/year=2020/month=01.parquet"
        doc = {"months": [{"object_key": key, "sha256": "a"*64, "parquet_bytes": 42, "rows": 10}]}
        new = translate_manifest(doc)
        self.assertEqual(new["months"][0]["object_key"], destination(key))
        self.assertEqual(new["months"][0]["sha256"], "a"*64)
        self.assertEqual(doc["months"][0]["object_key"], key)
        self.assertEqual(list(references(doc)), [(key, "a"*64, 42)])
        prices = {"partition_files": [{"path": "prices/year=2020/month=01.parquet", "sha256": "b"*64, "bytes": 12}]}
        self.assertEqual(translate_manifest(prices, True)["partition_files"][0]["path"],
                         "daily_prices/year=2020/month=01.parquet")
        self.assertEqual(list(references(prices, True))[0][0], SOURCE + prices["partition_files"][0]["path"])

    def test_fail_closed_on_missing_archive(self):
        with self.assertRaisesRegex(RuntimeError, "Missing required"):
            required_keys("daily_prices", {})
        with self.assertRaises(RuntimeError):
            list(references({"object_key": SOURCE + "prices/x", "bytes": 1}))
