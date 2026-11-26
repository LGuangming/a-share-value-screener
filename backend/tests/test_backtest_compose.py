"""Check the rendered standalone Compose contract, not production configuration."""
import json
import os
import subprocess
import unittest


class BacktestComposeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("TEST_BACKTEST_COMPOSE")=="1","requires Docker Compose")
    def test_isolation(self):
        env={**os.environ,"BACKTEST_IMAGE":"ashare-worker:fixture",
             "ARCHIVE_DIR":"/tmp/fixture-archive","BACKTEST_DIR":"/tmp/fixture-backtest"}
        result=subprocess.check_output(["docker","compose","-f","compose.backtest.yaml","config","--format","json"],env=env,text=True)
        config=json.loads(result)
        self.assertEqual(config["name"],"ashare-backtest")
        self.assertEqual(set(config["services"]),{"backtest-worker"})
        svc=config["services"]["backtest-worker"]
        self.assertEqual(svc["network_mode"],"none")
        self.assertTrue(svc["read_only"])
        self.assertEqual(svc["user"],"10002:10010")
        self.assertNotIn("env_file",svc)
        self.assertFalse(svc.get("ports"))
        self.assertEqual(set(svc["environment"]),{"TMPDIR","DUCKDB_TEMP_DIRECTORY"})
        volumes={v["target"]:v for v in svc["volumes"]}
        self.assertEqual(set(volumes),{"/data/archive","/market","/financial","/security","/evidence","/manifests","/scratch"})
        self.assertTrue(volumes["/data/archive"]["read_only"])
        self.assertTrue(volumes["/evidence"]["read_only"])
        self.assertTrue(all(not v["bind"]["create_host_path"] for v in volumes.values()))
