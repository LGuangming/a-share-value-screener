import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from bridge import TITLE,SCHEMA,parse_request,run,GitHub

NOW=datetime(2026,11,27,12,tzinfo=timezone.utc)
def request(task="status",**kw):
    return {"title":TITLE,"state":"open","number":7,"id":77,
        "created_at":NOW.isoformat(),"user":{"id":123,"type":"User"},
        "body":json.dumps({"schema":SCHEMA,"task":task,**kw})}

class Fake:
    def __init__(self,issue):
        self.issue=issue;self.reports=[];self.closed=[];self.fail=False
    def verify_repo(self,id):
        if id!=8:raise ValueError("wrong repo")
    def issues(self):return [self.issue]
    def call(self,path):return self.issue
    def report(self,n,doc):
        if self.fail:raise OSError("offline")
        self.reports.append((n,doc))
    def close(self,n):self.closed.append(n)

class BridgeTests(unittest.TestCase):
    def test_strict_request(self):
        self.assertIsNotNone(parse_request(request(),{123},NOW))
        for x in (request("shell",command="id"),request("run_profile",profile="../root"),
                  request("status",extra=True),{**request(),"user":{"id":124,"type":"User"}},
                  {**request(),"created_at":(NOW-timedelta(hours=1)).isoformat()},
                  {**request(),"pull_request":{}},
                  {**request(),"body":'{"schema":"ashare-vps-control-v1","task":"status","task":"status"}'}):
            self.assertIsNone(parse_request(x,{123},NOW))
    def test_private_repo_guard(self):
        gh=GitHub("fake","owner/repo")
        for doc in ({"private":False,"visibility":"public","id":8,"has_issues":True},
                    {"private":True,"visibility":"private","id":9,"has_issues":True}):
            gh.call=lambda path:doc
            with self.assertRaises(ValueError):gh.verify_repo(8)
    def test_delivery_failure_does_not_repeat_execution(self):
        cfg={"repository_id":8,"actor_ids":[123],"profiles":{}}
        gh=Fake(request());calls=[]
        def execute(doc,profiles):
            calls.append(doc);return {"status":"completed"}
        with TemporaryDirectory() as tmp:
            state=Path(tmp);gh.fail=True
            with self.assertRaises(OSError):run(cfg,gh,state,NOW,execute)
            gh.fail=False;run(cfg,gh,state,NOW,execute)
            run(cfg,gh,state,NOW,execute)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(gh.reports),1)
            self.assertEqual(gh.closed,[7])
    def test_interrupted_task_never_retries(self):
        cfg={"repository_id":8,"actor_ids":[123],"profiles":{}}
        gh=Fake(request())
        with TemporaryDirectory() as tmp:
            state=Path(tmp)
            (state/"issue-77.json").write_text(json.dumps({"repository_id":8,"issue_number":7,
                "issue_id":77,"request_sha256":"fixture","delivery":"running","result":None}))
            run(cfg,gh,state,NOW,lambda *_:self.fail("must not execute"))
            self.assertEqual(gh.reports[0][1]["status"],"interrupted_unknown_do_not_retry")
    def test_changed_issue_rejected(self):
        cfg={"repository_id":8,"actor_ids":[123],"profiles":{}}
        gh=Fake(request());gh.call=lambda path:request("run_profile",profile="backtest")
        with TemporaryDirectory() as tmp:
            run(cfg,gh,Path(tmp),NOW,lambda *_:self.fail("must not execute"))
            self.assertFalse(gh.reports)
if __name__=="__main__":unittest.main()
