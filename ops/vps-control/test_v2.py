import json
import os
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from bridge import diagnostics,inventory,clean_report,bounded_json,parse_request,SCHEMA
from research_runner import command
from docker_snapshot import snapshot
from test_bridge import request,NOW

class V2Tests(unittest.TestCase):
    def test_exact_remote_schema(self):
        for name in ("capabilities","data_inventory","task_status"):
            self.assertIsNotNone(parse_request(request(name),{123},NOW))
        for name in ("backtest_status","task_logs","docker_status","deployment_status"):
            self.assertIsNotNone(parse_request(request(name,report="jan"),{123},NOW))
            self.assertIsNone(parse_request(request(name,report="../etc/passwd"),{123},NOW))
            self.assertIsNone(parse_request(request(name,report="jan",path="/etc/passwd"),{123},NOW))
    def test_inventory_presence_and_symlink_exclusion(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/"year=2025"/"month=01";p.mkdir(parents=True)
            (p/"manifest.json").write_text("{}")
            d=root/"year=2026"/"month=02"/"day=20260202";d.mkdir(parents=True)
            (d/"manifest.json").write_text("{}")
            (p/"day=20250103").symlink_to(d,target_is_directory=True)
            a=inventory("market",root)
            self.assertEqual(a["monthly_receipts"],1)
            self.assertEqual(a["daily_receipts"],1)
            self.assertEqual(a["years"],["2025","2026"])
            (root/"fake.json").symlink_to(p/"manifest.json")
            with self.assertRaises(OSError):bounded_json(root/"fake.json")
            (root/"outside").symlink_to(p,target_is_directory=True)
            with self.assertRaises(OSError):bounded_json(root/"outside"/"manifest.json")
    def test_traverse_only_ancestor(self):
        with TemporaryDirectory() as tmp:
            parent=Path(tmp)/"traverse";root=parent/"market"
            part=root/"year=2025"/"month=01"
            part.mkdir(parents=True);(part/"manifest.json").write_text("{}")
            parent.chmod(0o100)
            try:
                self.assertEqual(inventory("market",root)["monthly_receipts"],1)
                self.assertEqual(bounded_json(part/"manifest.json"),{})
            finally:
                parent.chmod(0o700)
    def test_sanitized_snapshots_and_alias_type(self):
        doc={"status":"BT05_PREFLIGHT_RESEARCH_ONLY","research_preflight_pass":False,
             "checks":{"calendar":{"ok":True,"sessions":3,"error":"SECRET"},
                       "market_security":{"ok":False,"error":"/etc/token"}},
             "blockers":["market_security","secret"],"resource_observation":{"elapsed_seconds":2},
             "inputs":{"secret":"GITHUB_TOKEN"}}
        out=clean_report("preflight",doc)
        text=json.dumps(out)
        self.assertNotIn("SECRET",text);self.assertNotIn("token",text);self.assertNotIn("secret",text)
        self.assertEqual(out["blockers"],["market_security"])
        with TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/"report.json";p.write_text(json.dumps(doc))
            cfg={"repository_id":8,"profiles":{},"reports":{"jan":{"kind":"preflight","file":str(p)}}}
            result=diagnostics({"task":"backtest_status","report":"jan"},cfg,root)
            self.assertEqual(result["status"],"completed")
            self.assertEqual(diagnostics({"task":"task_logs","report":"jan"},cfg,root)["status"],"rejected")
            self.assertEqual(diagnostics({"task":"backtest_status","report":"unknown"},cfg,root)["status"],"rejected")
    def test_docker_and_fixed_execution(self):
        snap=snapshot({"web":"web-1","worker":"worker-1"},"web-1\trunning\nsecret-db\trunning\n",100)
        self.assertEqual(snap["containers"],{"web":"running","worker":"not_found"})
        self.assertNotIn("secret-db",json.dumps(snap))
        d={k:"/local/"+k for k in ("python","market","security","dividend","calendar","benchmark","actions","fees","warmup","start","end")}
        d.update(shards=2,execution_config=None,walk_plan=None)
        args=command("preflight",d,Path("/work"))
        self.assertIn("value_screener.bt05_preflight",args)
        self.assertIn("/work/preflight.json",args)
        self.assertNotIn("shell",args)
        with self.assertRaises(ValueError):command("shell",d,Path("/work"))
        with self.assertRaises(ValueError):command("walk",d,Path("/work"))

if __name__=="__main__":unittest.main()
