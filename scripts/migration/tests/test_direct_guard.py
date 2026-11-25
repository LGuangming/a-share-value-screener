import unittest
from direct_restore import empty_target, assert_no_user_relations, DirectError
from unittest.mock import Mock

class DirectCatalogGuard(unittest.TestCase):
    def test_single_public_schema_row(self):
        doc={k:[] for k in ("relations","functions","types","rules","large_objects","policies","triggers")}
        doc.update(schemas=[["public"]],special_objects={},extensions=[["plpgsql","1.0","pg_catalog"]])
        empty_target(doc)
        for bad in ({**doc,"schemas":["public"]},{**doc,"schemas":[["public"],["extra"]]},{**doc,"relations":[["public","existing","r"]]}):
            with self.assertRaisesRegex(DirectError,"TARGET_NOT_EMPTY"):
                empty_target(bad)

    def test_foreign_owned_relation_blocked_before_data_scan(self):
        c=Mock()
        c.execute.return_value.fetchone.return_value=(True,)
        with self.assertRaisesRegex(DirectError,"TARGET_NOT_EMPTY"):
            assert_no_user_relations(c)
        self.assertEqual(c.execute.call_count,1)
        self.assertIn("pg_class",c.execute.call_args.args[0])
