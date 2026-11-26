import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from diagnose_worker_identity import archive_info,compare,digest

class DiagnosticTests(unittest.TestCase):
    def test_saved_config_and_actual_layer_hash(self):
        raw_layer=b"fixture"
        config={"os":"linux","architecture":"amd64","rootfs":{"diff_ids":[digest(raw_layer)]},"config":{}}
        cfg=json.dumps(config).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"image.tar"
            with tarfile.open(path,"w") as tar:
                for name,data in [("config.json",cfg),("layer.tar",raw_layer),
                                  ("manifest.json",json.dumps([{"Config":"config.json","Layers":["layer.tar"]}]).encode())]:
                    entry=tarfile.TarInfo(name);entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
            result=archive_info(path)
        self.assertEqual(result["config_sha"],digest(cfg))
        self.assertTrue(result["all_layers_verified"])
    def test_equal_layers_different_config_never_approves(self):
        a={"config_sha":"a","config":{"config":{},"history":[1]},"diff_ids":["x"],"all_layers_verified":True}
        b={"config_sha":"b","config":{"config":{},"history":[2]},"diff_ids":["x"],"all_layers_verified":True}
        inspect={"Id":"a","Os":"linux","Architecture":"amd64","Config":{},"RootFS":{"Layers":["x"]}}
        result=compare(a,b,inspect,{**inspect,"Id":"b"},"b","a")
        self.assertTrue(result["rootfs_diffids_equal"])
        self.assertTrue(result["runtime_config_equal"])
        self.assertFalse(result["registry_config_matches_accepted_id"])
        self.assertEqual(result["config_top_level_differences"],["history"])
        self.assertFalse(result["release_approved"])
