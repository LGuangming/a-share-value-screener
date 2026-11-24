import unittest
from value_screener.raw_legacy_retirement import validate_remaining


class LegacyRetirementTests(unittest.TestCase):
    def test_inventory_must_match_exactly(self):
        entries={"a":{"source_bytes":10,"source_etag":"e"}}
        found={"a":{"size":10,"etag":"e"}}
        validate_remaining(found,entries,False)
        with self.assertRaises(RuntimeError):
            validate_remaining({},entries,False)
        validate_remaining({},entries,True)
        with self.assertRaises(RuntimeError):
            validate_remaining({"b":{"size":10,"etag":"e"}},entries,True)
        with self.assertRaises(RuntimeError):
            validate_remaining({"a":{"size":11,"etag":"e"}},entries,True)
