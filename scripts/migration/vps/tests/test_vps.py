import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops_dispatch import validate,dispatch
from control import identity,stop

ROOT=Path("/opt/ashare/migration")
def fixture():
    return {"Name":"/ashare-migration-postgres","Id":"a"*64,
        "Config":{"Image":"postgres:18.7-alpine","Labels":{"com.docker.compose.project":"ashare-migration","com.docker.compose.service":"postgres","io.ashare.scope":"migration-only"}},
        "Mounts":[{"Destination":"/var/lib/postgresql","Type":"volume","Name":"ashare-migration-pgdata","RW":True},
                  {"Destination":"/run/migration-secrets","Type":"bind","Source":str(ROOT/"config/private/db-secrets"),"RW":False},
                  {"Destination":"/docker-entrypoint-initdb.d","Type":"bind","Source":str(ROOT/"bin/db-init"),"RW":False}],
        "HostConfig":{"Memory":800*1024**2,"MemorySwap":800*1024**2,"NanoCpus":1500000000,"Privileged":False,"PortBindings":{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"55432"}]}},
        "NetworkSettings":{"Networks":{"ashare-migration-private":{}}},"State":{"Running":True}}

class VpsTest(unittest.TestCase):
    def test_command_exact_match(self):
        for cmd in ("","whoami","data-capacity ","data-inventory;id","migration-status /etc/shadow","ssh -L","ping","status"):
            with self.subTest(cmd=cmd),self.assertRaises(PermissionError):
                dispatch(cmd)
    def test_summary_schema_and_expiry(self):
        good={"captured_at_epoch":1000,"passed":True,"checks":2,"failed_checks":0}
        self.assertEqual(validate("migration-verify",good,1001),good)
        for bad in ({**good,"token":"secret"},{**good,"captured_at_epoch":0},{**good,"captured_at_epoch":1002},{**good,"checks":True},{**good,"failed_checks":1}):
            with self.assertRaises(ValueError): validate("migration-verify",bad,1001)
    def test_container_identity_rejects_lookalikes(self):
        self.assertEqual(identity(fixture(),ROOT),"a"*64)
        for edit in (lambda x:x.update(Name="/ashare-postgres"),lambda x:x["Config"]["Labels"].update({"io.ashare.scope":"production"}),lambda x:x["Mounts"][0].update(Name="production"),lambda x:x["Mounts"][1].update(RW=True),lambda x:x["HostConfig"].update(Privileged=True),lambda x:x["NetworkSettings"]["Networks"].update({"ashare-db":{}})):
            obj=fixture();edit(obj)
            with self.assertRaises(ValueError): identity(obj,ROOT)
    def test_stop_uses_exact_id_and_no_delete(self):
        first=fixture();after=copy.deepcopy(first);after["State"]["Running"]=False
        with patch("control.inspect_container",side_effect=[first,after]),patch("control.docker") as command:
            stop(ROOT)
            command.assert_called_once_with("stop","--time","30","a"*64)
    def test_stop_rejects_wrong_identity_without_docker(self):
        first=fixture();first["Name"]="/ashare-postgres"
        with patch("control.inspect_container",return_value=first),patch("control.docker") as command:
            with self.assertRaises(ValueError): stop(ROOT)
            command.assert_not_called()
