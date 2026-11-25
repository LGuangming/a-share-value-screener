import unittest
from direct_restore import empty_target, DirectError

class DirectCatalogGuard(unittest.TestCase):
    def test_single_public_schema_row(self):
        doc={k:[] for k in ("relations","functions","types","rules","large_objects","policies","triggers")}
        doc.update(schemas=[["public"]],special_objects={},extensions=[["plpgsql","1.0","pg_catalog"]])
        empty_target(doc)
        for bad in ({**doc,"schemas":["public"]},{**doc,"schemas":[["public"],["extra"]]},{**doc,"relations":[["public","existing","r"]]}):
            with self.assertRaisesRegex(DirectError,"TARGET_NOT_EMPTY"):
                empty_target(bad)
