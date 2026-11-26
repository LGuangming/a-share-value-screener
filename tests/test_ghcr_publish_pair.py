import copy
import datetime as dt
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch
import ghcr_publish_pair as p

WEB = "sha256:" + "1"*64
WORKER = "sha256:" + "2"*64
WD = "sha256:" + "3"*64
KD = "sha256:" + "4"*64
ENV = {"GITHUB_REPOSITORY": p.REPO, "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
       "GITHUB_ACTOR": "LGuangming", "GITHUB_TRIGGERING_ACTOR": "LGuangming", "REVIEWED_SHA": p.SOURCE,
       "REVIEWED_RUN": p.REVIEWED_RUN, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "CONFIRM": p.NORMAL}
def pair(origin="123"):
    return {"source": p.SOURCE, "reviewed_run": p.REVIEWED_RUN, "run_id": origin, "attempt": "1", "web": WEB, "minimal": WORKER}

class Fake:
    def __init__(self):
        self.calls = []
        self.pushed = {}
        self.tags = {}
        self.fail = None
    def check(self, op, kind):
        self.calls.append((op, kind))
        if self.fail == (op, kind):
            raise p.Blocked("injected failure")
    def metadata(self, kind):
        self.check("metadata", kind)
    def login(self):
        self.check("login", None)
    def manifest(self, kind, ref, anonymous=False):
        self.check("anonymous" if anonymous else "manifest", kind)
        if anonymous:
            return "denied"
        d = self.tags.get(kind)
        return d if ref.startswith("test-pr28-") or ref == d else None
    def push(self, kind, tag):
        self.check("push", kind)
        self.pushed[kind] = self.tags[kind] = WD if kind == "web" else KD
        return self.tags[kind]
    def pull_check(self, kind, digest, expected):
        self.check("pull", kind)
        if expected != (WEB if kind == "web" else WORKER):
            raise p.Blocked("wrong accepted image")

class PairTests(unittest.TestCase):
    def go(self, r, env=ENV, accepted=None):
        self.reports = []
        return p.run(env, pair() if accepted is None else accepted, r, lambda x:self.reports.append(copy.deepcopy(x)))
    def recovery(self, r):
        return self.go(r, dict(ENV, CONFIRM=p.RECOVER), pair(p.PRIOR_RUN))
    def test_normal_pair(self):
        r = Fake()
        out = self.go(r)
        self.assertTrue(out["complete"])
        self.assertFalse(out["deployment_authorized"])
        self.assertEqual(r.pushed, {"web": WD, "worker": KD})
        self.assertTrue(all(x["stage"] == "verified" for x in out["images"].values()))
    def test_auth_and_wrong_pair_no_calls(self):
        for key,val in [("GITHUB_EVENT_NAME","pull_request"),("GITHUB_REF","refs/heads/other"),
                        ("GITHUB_ACTOR","other"),("GITHUB_TRIGGERING_ACTOR","other"),("GITHUB_RUN_ATTEMPT","2"),
                        ("GITHUB_REPOSITORY","other/repo"),("CONFIRM","bad"),("REVIEWED_SHA","bad")]:
            r = Fake()
            with self.assertRaises(p.Blocked): self.go(r, dict(ENV, **{key:val}))
            self.assertFalse(r.calls)
        r = Fake()
        with self.assertRaises(p.Blocked): self.go(r, accepted=pair("999"))
        self.assertFalse(r.calls)
    def test_existing_worker_blocks_both(self):
        r = Fake()
        r.tags["worker"] = KD
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertFalse(r.pushed)
    def test_private_gate_precedes_write(self):
        r = Fake()
        r.fail = ("metadata","worker")
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertFalse(r.pushed)
    def test_partial_receipt_after_push(self):
        r = Fake()
        r.fail = ("pull","web")
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertEqual(r.pushed, {"web": WD})
        out = self.reports[-1]
        self.assertFalse(out["complete"])
        self.assertEqual(out["images"]["web"]["pushed_digest"], WD)
        self.assertEqual(out["failure_stage"], "web:verify_authenticated_pull")
    def test_push_ambiguous_preserved(self):
        r = Fake()
        r.fail = ("push","web")
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertEqual(self.reports[-1]["images"]["web"]["stage"], "push_attempted")
        self.assertFalse(self.reports[-1]["complete"])
    def test_anonymous_ambiguous_blocks_worker(self):
        r = Fake()
        r.fail = ("anonymous","web")
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertNotIn("worker",r.pushed)
    def test_recover_only_worker(self):
        r = Fake()
        r.tags["web"] = p.PRIOR_WEB
        out = self.recovery(r)
        self.assertTrue(out["complete"])
        self.assertEqual(r.pushed, {"worker": KD})
        self.assertTrue(out["images"]["web"]["retained_without_write"])
        self.assertEqual(out["accepted_run_id"], p.PRIOR_RUN)
        self.assertIn("run-"+p.PRIOR_RUN+"-a1",out["images"]["worker"]["tag"])
        self.assertLess(r.calls.index(("pull","web")),r.calls.index(("push","worker")))
    def test_recovery_wrong_web_or_existing_worker_blocks(self):
        for tags in ({}, {"web": WD}, {"web":p.PRIOR_WEB,"worker":KD}):
            r = Fake()
            r.tags.update(tags)
            with self.assertRaises(p.Blocked): self.recovery(r)
            self.assertFalse(r.pushed)
    def test_recovery_bad_retained_pull_blocks(self):
        r = Fake()
        r.tags["web"] = p.PRIOR_WEB
        r.fail = ("pull","web")
        with self.assertRaises(p.Blocked): self.recovery(r)
        self.assertFalse(r.pushed)
    def test_remote_digest_mismatch_stops(self):
        r = Fake()
        original = r.manifest
        r.manifest = lambda k,ref,anonymous=False: WD if k=="web" and ref.startswith("test-pr28-") and k in r.pushed else original(k,ref,anonymous)
        # Wrong immutable digest response.
        r.manifest = Mock(side_effect=lambda k,ref,anonymous=False: KD if ref == WD else original(k,ref,anonymous))
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertNotIn("worker", r.pushed)
    def test_final_privacy_failure_no_complete(self):
        r = Fake()
        old = r.metadata
        def meta(k):
            if len(r.pushed)==2 and k=="web": raise p.Blocked("privacy changed")
            old(k)
        r.metadata = meta
        with self.assertRaises(p.Blocked): self.go(r)
        self.assertFalse(self.reports[-1]["complete"])
        self.assertEqual(self.reports[-1]["failure_stage"],"web:final_recheck")

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.r=p.Registry(self.tmp.name,"fake-secret")
        self.tag="test-pr28-"+p.SOURCE+"-run-123-a1"
        self.body=b'{"schemaVersion":2}'
        self.digest="sha256:"+hashlib.sha256(self.body).hexdigest()
        self.h={"Content-Type":p.MEDIA[0],"Docker-Content-Digest":self.digest}
    def tearDown(self): self.tmp.cleanup()
    def response(self,status,body=b"",headers=None):
        self.r.request=Mock(side_effect=[(200,{},b'{"token":"fake-bearer"}'),(status,headers or {},body)])
    def test_raw_digest(self):
        self.response(200,self.body,self.h)
        self.assertEqual(self.r.manifest("web",self.tag),self.digest)
        calls=self.r.request.call_args_list
        self.assertTrue(calls[0].args[1]["Authorization"].startswith("Basic "))
        self.assertEqual(calls[1].args[1]["Authorization"],"Bearer fake-bearer")
    def test_wrong_header_blocks(self):
        self.response(200,self.body,dict(self.h,**{"Docker-Content-Digest":WD}))
        with self.assertRaises(p.Blocked): self.r.manifest("web",self.tag)
    def test_absence_only_explicit(self):
        self.response(404,b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}')
        self.assertIsNone(self.r.manifest("web",self.tag))
        for status,body in [(404,b'{"errors":[{"code":"DENIED"}]}'),(403,b""),(429,b""),
                            (404,b'{"errors":[{"code":"MANIFEST_UNKNOWN"},{"code":"UNAUTHORIZED"}]}')]:
            self.response(status,body)
            with self.assertRaises(p.Blocked): self.r.manifest("web",self.tag)
    def test_anonymous_denial(self):
        self.response(403)
        self.assertEqual(self.r.manifest("web",WD,True),"denied")
        self.assertNotIn("Authorization",self.r.request.call_args_list[0].args[1])
        self.response(200,self.body,self.h)
        self.assertNotEqual(self.r.manifest("web",WD,True),"denied")
        self.response(404,b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}')
        with self.assertRaises(p.Blocked): self.r.manifest("web",WD,True)
    def test_no_buildx_and_safe_exit_code(self):
        with patch("subprocess.run",return_value=Mock(returncode=255,stdout="",stderr="fake-secret")) as mock:
            with self.assertRaisesRegex(p.Blocked,"docker pull exit 255"):
                self.r.docker("pull","--platform","linux/amd64","image")
            self.assertNotIn("GHCR_TOKEN",mock.call_args.kwargs["env"])
    def test_metadata_identity(self):
        body={"name":p.package("web").split("/")[1],"package_type":"container","owner":{"login":"LGuangming"},
              "id":1,"visibility":"private","repository":{"full_name":p.REPO}}
        self.r.request=Mock(return_value=(200,{},json.dumps(body).encode()))
        self.r.metadata("web")
        for changes in ({"visibility":"public"},{"repository":None},{"name":"other"}):
            self.r.request=Mock(return_value=(200,{},json.dumps(dict(body,**changes)).encode()))
            with self.assertRaises(p.Blocked): self.r.metadata("web")

class ArtifactTests(unittest.TestCase):
    def fixtures(self):
        r={"id":int(p.PRIOR_RUN),"head_sha":p.PRIOR_HEAD,"head_branch":"main","event":"workflow_dispatch",
           "path":".github/workflows/ghcr-test-images.yml","status":"completed","conclusion":"failure","run_attempt":1,
           "repository":{"id":1468368990,"full_name":p.REPO},"actor":{"login":"LGuangming"},"updated_at":"2026-11-26T02:25:11Z"}
        def job(name,c,steps):
            return {"name":name,"conclusion":c,"status":"completed","started_at":"2026-11-26T02:18:50Z","completed_at":"2026-11-26T02:25:10Z",
                    "steps":[{"name":n,"status":"completed","conclusion":s} for n,s in steps]}
        js={"total_count":3,"jobs":[job("reviewed-pair / resolve-base-images","success",[]),
          job("reviewed-pair / Build and accept exact Web + Minimal pair (read only)","success",[
            ("Seal accepted pair and verify save/load identity (no registry)","success"),("Transfer accepted pair only on authorized future dispatch","success")]),
          job("Publish previously accepted private test pair (explicit dispatch only)","failure",[
            ("Revalidate artifact, both image IDs and time-limited risk policy before any push","success"),
            ("Require both dedicated packages already confirmed private before any push","success"),
            ("Push accepted pair and independently verify both immutable digests","failure")])]}
        a={"id":p.PRIOR_ARTIFACT,"name":"accepted-pair-"+p.PRIOR_RUN+"-a1","digest":p.PRIOR_ZIP,"size_in_bytes":179945528,
           "expired":False,"created_at":"2026-11-26T02:24:44Z","expires_at":"2026-11-27T02:24:43Z",
           "workflow_run":{"id":int(p.PRIOR_RUN),"head_sha":p.PRIOR_HEAD,"head_branch":"main","repository_id":1468368990,"head_repository_id":1468368990}}
        return r,js,a
    def test_exact_recovery_provenance(self):
        self.assertTrue(p.validate_recovery(*self.fixtures(),p.stamp("2026-11-26T03:00:00Z")))
    def test_bad_artifact_or_expiry_blocks(self):
        for changes in ({"id":1},{"digest":WD},{"expired":True},{"name":"other"}):
            r,j,a=self.fixtures()
            a.update(changes)
            with self.assertRaises(p.Blocked): p.validate_recovery(r,j,a,p.stamp("2026-11-26T03:00:00Z"))
        with self.assertRaises(p.Blocked): p.validate_recovery(*self.fixtures(),p.stamp("2026-11-27T03:00:00Z"))
    def test_failed_original_gate_blocks(self):
        r,j,a=self.fixtures()
        j["jobs"][1]["steps"][0]["conclusion"]="failure"
        with self.assertRaises(p.Blocked): p.validate_recovery(r,j,a,p.stamp("2026-11-26T03:00:00Z"))
    def test_zip_hash_and_members(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            z=root/"test.zip"
            with zipfile.ZipFile(z,"w") as f:
                for name in p.FILES: f.writestr(name,b"fixture")
            with self.assertRaises(p.Blocked): p.extract_recovery(z,root/"out")
            with patch.object(p,"PRIOR_ZIP",p.hash_file(z)):
                p.extract_recovery(z,root/"out")
            self.assertEqual({x.name for x in (root/"out").iterdir()},p.FILES)
            with zipfile.ZipFile(z,"a") as f: f.writestr("../escape",b"bad")
            with patch.object(p,"PRIOR_ZIP",p.hash_file(z)):
                with self.assertRaises(p.Blocked): p.extract_recovery(z,root/"out2")
    def test_checksum_exact_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            for name in p.FILES-{"SHA256SUMS"}: (root/name).write_bytes(b"fixture")
            checks="".join(p.hash_file(root/name)[7:]+"  "+name+"\n" for name in sorted(p.FILES-{"SHA256SUMS"}))
            (root/"SHA256SUMS").write_text(checks)
            p.verify_files(root)
            (root/"pair.json").write_bytes(b"bad")
            with self.assertRaises(p.Blocked): p.verify_files(root)
    def test_workflow_safety(self):
        w=(pathlib.Path(__file__).resolve().parents[1]/".github/workflows/ghcr-test-images.yml").read_text()
        for t in ("github.event_name == 'workflow_dispatch'","refs/heads/main","github.run_attempt == '1'",
                  "needs.reviewed-pair.result == 'success'","needs.reviewed-pair.result == 'skipped'","packages: write",
                  "reviewed-source/tests/candidate_vulnerability_gate.py","publication-progress.json","pair-release.json",
                  "actions/artifacts/15080908340/zip"):
            self.assertIn(t,w)
        self.assertNotIn("imagetools inspect",w)
        self.assertNotIn("docker push",w)
        self.assertNotIn("secrets: inherit",w)

if __name__=="__main__": unittest.main()
