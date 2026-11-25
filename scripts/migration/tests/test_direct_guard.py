import unittest
from direct_restore import empty_target, assert_no_user_relations, network_guard, DirectError
from unittest.mock import Mock, patch
import json

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

class DirectNetworkGuard(unittest.TestCase):
    def fixture(self,names,internal=True,extra_peer=False,driver="bridge",wrong_id=False):
        cid="a"*64
        info={"Id":cid,"NetworkSettings":{"Networks":{n:{"NetworkID":n+"-id"} for n in names}}}
        def inspect(*args):
            name=args[-1]
            return json.dumps([{"Name":name,"Id":"wrong" if wrong_id else name+"-id",
                "Driver":driver,"Internal":internal if name=="ashare-db" else False,
                "Containers":{cid:{},**({"unexpected":{}} if extra_peer else {})}}]).encode()
        return info,inspect

    def test_only_reviewed_network_combinations(self):
        for names in (["ashare-db"],["ashare-db","1panel-network"]):
            info,inspect=self.fixture(names)
            with patch("direct_restore.docker",side_effect=inspect):
                network_guard(info)
        for names in ([],["1panel-network"],["ashare-db","unknown"],["ashare-db","1panel-network","unknown"]):
            info,inspect=self.fixture(names)
            with patch("direct_restore.docker",side_effect=inspect):
                with self.assertRaisesRegex(DirectError,"NETWORK_MEMBERSHIP"):
                    network_guard(info)

    def test_network_identity_and_isolation(self):
        for opts,code in (({"internal":False},"NETWORK_NOT_INTERNAL"),
                          ({"extra_peer":True},"MANAGEMENT_NETWORK_SCOPE"),
                          ({"driver":"host"},"NETWORK_IDENTITY"),
                          ({"wrong_id":True},"NETWORK_IDENTITY")):
            info,inspect=self.fixture(["ashare-db","1panel-network"],**opts)
            with patch("direct_restore.docker",side_effect=inspect):
                with self.assertRaisesRegex(DirectError,code):
                    network_guard(info)
