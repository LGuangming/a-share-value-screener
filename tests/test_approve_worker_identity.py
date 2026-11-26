import unittest
from approve_worker_identity import approve, REQUIRED_TRUE

ID="sha256:"+"a"*64
CFG="sha256:"+"b"*64
REV="c"*40

def report():
    return {**{k:True for k in REQUIRED_TRUE},
        "expected_accepted_image_id":ID,"loaded_local_image_id":ID,"pulled_remote_image_id":ID,
        "local_archive_config_sha256":CFG,"registry_manifest_config_digest":CFG,
        "pulled_archive_config_sha256":CFG,"local_revision":REV,"remote_revision":REV,
        "local_diffids":["layer"],"remote_diffids":["layer"],
        "registry_config_matches_accepted_id":False,"release_approved":False}

class AcceptanceTests(unittest.TestCase):
    def test_distinct_digest_namespaces_accepted_only_when_independently_equal(self):
        result=approve(report(),ID,CFG,REV)
        self.assertTrue(result["release_approved"])
        self.assertEqual(result["verified_layer_count"],1)
    def test_every_integrity_flag_required(self):
        for key in REQUIRED_TRUE:
            for value in (False,None):
                with self.subTest(key=key,value=value):
                    r=report();r[key]=value
                    with self.assertRaises(RuntimeError):
                        approve(r,ID,CFG,REV)
    def test_identity_revision_or_layer_mismatch_blocks(self):
        for key in ("expected_accepted_image_id","loaded_local_image_id","pulled_remote_image_id",
                    "local_archive_config_sha256","registry_manifest_config_digest",
                    "pulled_archive_config_sha256","local_revision","remote_revision",
                    "local_diffids","remote_diffids"):
            with self.subTest(key=key):
                r=report();r[key]="wrong"
                with self.assertRaises(RuntimeError):
                    approve(r,ID,CFG,REV)
    def test_empty_layers_block(self):
        r=report();r["local_diffids"]=r["remote_diffids"]=[]
        with self.assertRaises(RuntimeError):
            approve(r,ID,CFG,REV)
