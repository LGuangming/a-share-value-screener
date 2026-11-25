import unittest
from direct_restore import empty_target, assert_no_user_relations, network_guard, DirectError
from unittest.mock import Mock, patch
import json
import copy
from direct_restore import approved_source_scope, compare, TABLES, NEON_AUTH_TABLES, SCHEMA_FIELDS

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


class DirectSourceScope(unittest.TestCase):
    def fixture(self,auth=True):
        pairs=[("public",t) for t in sorted(TABLES)]
        if auth:
            pairs=[("neon_auth",t) for t in sorted(NEON_AUTH_TABLES)]+pairs
        doc={k:[] for k in SCHEMA_FIELDS}
        doc.update(schemas=[["neon_auth"],["public"]] if auth else [["public"]],
                   tables=[{"schema":s,"name":t,"rows":1 if s=="public" or t=="project_config" else 0,"sha256":"fixture"} for s,t in pairs],
                   relations=[[s,t,"r","p",None,None] for s,t in pairs],
                   columns=[[s,t,1,"id","integer"] for s,t in pairs],
                   indexes=[[s,t+"_pkey","CREATE UNIQUE INDEX fixture"] for s,t in pairs],
                   constraints=[[s,t,t+"_pkey","PRIMARY KEY (id)",True] for s,t in pairs],
                   rls=[[s,t,False,False] for s,t in pairs],
                   special_objects={},large_objects=[],extensions=[["plpgsql","1.0","pg_catalog"]],
                   database=["neondb","180007",10],dump_sha256="full-backup")
        return doc

    def test_public_only_preserves_previous_behavior(self):
        doc=self.fixture(False)
        projected,scope=approved_source_scope(doc)
        self.assertEqual(projected,doc)
        self.assertEqual(scope["excluded_tables"],[])
        self.assertEqual(scope["restored_tables"],8)

    def test_auth_exclusion_requires_explicit_approval_and_keeps_source(self):
        doc=self.fixture()
        original=copy.deepcopy(doc)
        with self.assertRaisesRegex(DirectError,"SOURCE_TABLE_SCOPE"):
            approved_source_scope(doc)
        projected,scope=approved_source_scope(doc,True)
        self.assertEqual(doc,original)
        self.assertEqual(projected["schemas"],[["public"]])
        self.assertEqual(len(scope["excluded_tables"]),9)
        self.assertEqual(scope["excluded_rows"],1)
        self.assertEqual(scope["restored_rows"],8)
        self.assertEqual(projected["dump_sha256"],"full-backup")
        self.assertEqual(compare(projected,self.fixture(False)),[])
        target=copy.deepcopy(projected)
        target["tables"][0]["sha256"]="changed"
        self.assertEqual(compare(projected,target),["tables"])

    def test_unapproved_objects_and_auth_rows_blocked(self):
        for case,code in (("schema","SOURCE_TABLE_SCOPE"),("table","SOURCE_TABLE_SCOPE"),
                          ("relation","SOURCE_TABLE_SCOPE"),("auth_rows","NEON_AUTH_DATA_SCOPE"),
                          ("function","SOURCE_CUSTOM_OBJECTS"),("cross_fk","CROSS_SCHEMA_DEPENDENCY"),
                          ("catalog","SOURCE_CATALOG_SCOPE")):
            with self.subTest(case=case):
                doc=self.fixture()
                if case=="schema": doc["schemas"].append(["extra"])
                elif case=="table": doc["tables"].append({"schema":"neon_auth","name":"extra","rows":0})
                elif case=="relation": doc["relations"][0][2]="v"
                elif case=="auth_rows": doc["tables"][0]["rows"]=1
                elif case=="function": doc["functions"].append(["neon_auth","danger"])
                elif case=="cross_fk": doc["constraints"].append(["public","stocks","fk","FOREIGN KEY (id) REFERENCES neon_auth.user(id)",True])
                elif case=="catalog": doc["columns"].append(["extra","x",1,"id"])
                with self.assertRaisesRegex(DirectError,code):
                    approved_source_scope(doc,True)
