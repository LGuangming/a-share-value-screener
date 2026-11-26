import importlib.util
import pathlib
import re
import unittest
from unittest.mock import patch, Mock
import tempfile
import json

spec=importlib.util.spec_from_file_location("init",pathlib.Path(__file__).resolve().parents[1]/"deploy/vps_test_init.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
PIN="postgres@sha256:"+"a"*64

class Tests(unittest.TestCase):
    def test_random_distinct_and_stable(self):
        s=m.state_new(PIN);m.state_check(s,PIN)
        self.assertNotEqual(s,m.state_new(PIN))
        self.assertEqual(m.config(s),m.config(s))
        self.assertEqual(len(set(s[k] for k in ("admin","reader","writer","site","session","owner_id"))),6)
    def test_state_rejects_wrong_pin(self):
        with self.assertRaises(m.Blocked):m.state_check(m.state_new(PIN),"postgres@sha256:"+"b"*64)
    def test_state_rejects_duplicate_password(self):
        s=m.state_new(PIN);s["reader"]=s["writer"]
        with self.assertRaises(m.Blocked):m.state_check(s,PIN)
    def test_pin_strict(self):
        for x in ["postgres:18.7-alpine","evil/postgres@sha256:"+"a"*64,"postgres@sha256:bad"]:
            self.assertIsNone(m.PIN.fullmatch(x))
        self.assertIsNotNone(m.PIN.fullmatch(PIN))
    def test_config_separation(self):
        s=m.state_new(PIN);c=m.config(s)
        self.assertIn(b"ashare-test-db",c["config/.env"])
        self.assertIn(b"ASHARE_LOOPBACK_PORT=3198",c["config/.env"])
        self.assertIn(m.WEB.encode(),c["config/.env"])
        self.assertIn(m.WORKER.encode(),c["config/.env"])
        self.assertNotIn(s["writer"].encode(),c["config/web.env"])
        self.assertNotIn(s["reader"].encode(),c["config/worker.env"])
        self.assertNotIn(s["admin"].encode(),c["config/web.env"])
        self.assertIn(b"ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY=false",c["config/worker.env"])
    def test_sql_password_substitution(self):
        r=pathlib.Path(__file__).resolve().parents[1]
        files={k:(r/k).read_bytes() for k in m.BLOBS}
        s=m.state_new(PIN);sql=m.init_sql(files,s)
        self.assertNotIn("fixture_only",sql)
        self.assertIn(s["reader"],sql);self.assertIn(s["writer"],sql)
        self.assertTrue(sql.startswith("BEGIN;"));self.assertTrue(sql.endswith("COMMIT;\n"))
        self.assertIn("current_database()<>'ashare_test'",sql)
    def test_source_pins(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        self.assertEqual(set(m.sources(root)),set(m.BLOBS))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(m.Blocked):m.sources(pathlib.Path(d))
    def test_acl_count_and_scoped_delete(self):
        sql=m.acl_sql()
        for t in m.TABLES:self.assertIn("('%s')"%t,sql)
        self.assertIn("t.name IN ('daily_prices','price_history','price_history_fetches','screen_results')",sql)
        self.assertEqual(len(m.TABLES)*len(m.PRIVS)*len(m.ROLES),112)
    def test_partial_schema_rejected(self):
        with patch.object(m,"psql",side_effect=["7","2"]) as q:
            with self.assertRaises(m.Blocked):m.initialize_db({},m.state_new(PIN))
            self.assertEqual(q.call_count,2)
    def test_resume_skips_ddl(self):
        with patch.object(m,"psql",side_effect=["8","2"]) as q,patch.object(m,"verify_acl") as v:
            m.initialize_db({},m.state_new(PIN));self.assertEqual(q.call_count,2);v.assert_called_once()
    def test_first_schema_transaction(self):
        with patch.object(m,"psql",side_effect=["0","0",""]) as q,patch.object(m,"verify_acl"),patch.object(m,"init_sql",return_value="BEGIN; COMMIT;"):
            m.initialize_db({},m.state_new(PIN));self.assertEqual(q.call_args.args[0],"BEGIN; COMMIT;")
    def test_network_fail_closed(self):
        s=m.state_new(PIN)
        n={"Name":m.NET,"Internal":True,"Driver":"bridge","Scope":"local","Labels":{m.LABEL:s["owner_id"]},"Containers":{},"Id":"x"}
        with patch.object(m,"read_json",return_value=n):self.assertEqual(m.verify_net(s),"x")
        for change in ({"Internal":False},{"Labels":{}},{"Containers":{"1":{"Name":"ashare-postgres"}}}):
            with patch.object(m,"read_json",return_value=dict(n,**change)):
                with self.assertRaises(m.Blocked):m.verify_net(s)
    def test_local_docker_not_remote(self):
        with patch.object(m,"run",return_value="ok") as r:
            m.docker("ps")
            self.assertEqual(r.call_args.args[0][:3],["docker","--host","unix:///var/run/docker.sock"])
    def test_password_not_in_argv(self):
        with patch.object(m,"docker",return_value="ok") as d:
            m.psql("SELECT 1;","screener_reader","super-secret")
            self.assertNotIn("super-secret",str(d.call_args.args))
            self.assertEqual(d.call_args.kwargs["data"],"super-secret\nSELECT 1;")
    def test_container_contract(self):
        s=m.state_new(PIN);iid="sha256:"+"a"*64
        d={"Name":"/"+m.DB,"Image":iid,"Config":{"Labels":{m.LABEL:s["owner_id"]},
            "Env":["POSTGRES_USER=fixture_admin","POSTGRES_DB=ashare_test","POSTGRES_PASSWORD="+s["admin"]],
            "Entrypoint":["docker-entrypoint.sh"],"Cmd":["postgres"]},
            "HostConfig":{"Memory":512*1024**2,"NanoCpus":1000000000,"RestartPolicy":{"Name":"no"}},
            "NetworkSettings":{"Networks":{m.NET:{"NetworkID":"n","Aliases":["postgres"]}}},
            "Mounts":[{"Type":"bind","Source":str(m.ROOT/"postgres"),"Destination":"/var/lib/postgresql","RW":True}]}
        with patch.object(m,"read_json",return_value=d):m.verify_container(s,iid,"n")
        for key,value in (("Mounts",[]),("Image","other"),("NetworkSettings",{"Networks":{"ashare-db":{}}})):
            with patch.object(m,"read_json",return_value=dict(d,**{key:value})):
                with self.assertRaises(m.Blocked):m.verify_container(s,iid,"n")
    def test_no_destructive_commands(self):
        script=(pathlib.Path(__file__).resolve().parents[1]/"deploy/vps_test_init.py").read_text()
        for command in ['docker("rm"','docker("network","rm"','docker("pull"','docker("compose","up"','shutil.rmtree','--privileged']:
            self.assertNotIn(command,script)
    def test_create_no_overwrite(self):
        # Unit runner is nonroot: patch restrictive file reader only for this test.
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/"file"
            m.create_once(p,b"initial")
            with patch.object(m,"regular",side_effect=lambda path,mode=0o600:path.read_bytes()):
                m.create_once(p,b"initial")
                with self.assertRaises(m.Blocked):m.create_once(p,b"changed")
            self.assertEqual(p.read_bytes(),b"initial")
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/"link";p.symlink_to("/tmp")
            with self.assertRaises(m.Blocked):m.safe_dir(p)
    def test_error_does_not_leak(self):
        with patch.object(m.subprocess,"run",return_value=Mock(returncode=1,stdout="",stderr="password-secret")):
            with self.assertRaisesRegex(m.Blocked,r"^local command failed \(exit 1\)$"):m.run(["x","password-secret"])
if __name__=="__main__":unittest.main()
