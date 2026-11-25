"""Exercise the actual inline diagnostic program, without a real API or token."""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import textwrap
import unittest
import urllib.error
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ghcr-package-readiness.yml"
PREFIX = "https://api.github.com/users/lguangming/packages/container/"
NAMES = ["a-share-value-screener-web-test-pr28", "a-share-value-screener-worker-test-pr28"]
VERSION_SUFFIX = "/versions?per_page=1"
SECRET = "offline-fixture-token-not-a-real-secret"
READY = "private_read_confirmed_write_unverified"


def metadata(name, visibility="private", **updates):
    body = {"name": name, "package_type": "container", "owner": {"login": "LGuangming"},
            "id": 100, "visibility": visibility,
            "repository": {"full_name": "LGuangming/a-share-value-screener"},
            "version_count": 1}
    body.update(updates)
    return body


def code():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])


class Response(io.BytesIO):
    status = 200


class OfflineHTTP:
    def __init__(self, overrides):
        self.overrides = overrides
        self.calls = []

    def open(self, request, timeout):
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == "Bearer " + SECRET
        assert timeout == 20
        url = request.full_url
        assert url.startswith(PREFIX)
        path = url.removeprefix(PREFIX)
        assert path in NAMES + [n + VERSION_SUFFIX for n in NAMES], path
        self.calls.append(path)
        value = self.overrides.get(path, [] if path.endswith(VERSION_SUFFIX) else metadata(path))
        if isinstance(value, int):
            raise urllib.error.HTTPError(url, value, "fixture", {}, None)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, bytes):
            return Response(value)
        return Response(json.dumps(value).encode())


class ReadinessTests(unittest.TestCase):
    def invoke(self, overrides=None):
        fake = OfflineHTTP(overrides or {})
        log = io.StringIO()
        with tempfile.TemporaryDirectory() as temp:
            summary = pathlib.Path(temp) / "summary.md"
            env = {"GH_TOKEN": SECRET, "GITHUB_REPOSITORY": "LGuangming/a-share-value-screener",
                   "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a"*40,
                   "RUNNER_TEMP": temp, "GITHUB_STEP_SUMMARY": str(summary)}
            with patch.dict(os.environ, env, clear=True), patch("urllib.request.build_opener", return_value=fake), contextlib.redirect_stdout(log):
                status = 0
                try:
                    exec(compile(code(), str(WORKFLOW), "exec"), {"__name__": "__main__"})
                except SystemExit as exc:
                    status = exc.code
            report = json.loads((pathlib.Path(temp) / "ghcr-readiness.json").read_text())
            self.assertNotIn(SECRET, log.getvalue())
            self.assertNotIn(SECRET, json.dumps(report))
            self.assertNotIn(SECRET, summary.read_text())
            self.assertFalse(report["write_permission_verified"])
            self.assertFalse(report["publication_authorized"])
            self.assertEqual([x["package"] for x in report["packages"]], NAMES)
            self.assertIn(NAMES[1], fake.calls, "second package must be checked")
            return status, report, fake.calls

    def test_private_pair_readable(self):
        status, report, calls = self.invoke()
        self.assertEqual(status, 0)
        self.assertTrue(report["both_private_and_readable"])
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(x["readiness"] == READY for x in report["packages"]))

    def test_404_is_ambiguous_and_blocks(self):
        status, report, calls = self.invoke({n: 404 for n in NAMES})
        self.assertEqual(status, 1)
        self.assertFalse(report["both_private_and_readable"])
        self.assertEqual(calls, NAMES)
        for p in report["packages"]:
            self.assertEqual(p["metadata_error"], "missing_or_inaccessible")
            self.assertEqual(p["exists"], "unconfirmed")

    def test_metadata_http_errors(self):
        for http in (301, 302, 400, 401, 403, 429, 500):
            with self.subTest(http=http):
                status, report, calls = self.invoke({NAMES[0]: http})
                self.assertEqual(status, 1)
                self.assertEqual(report["packages"][0]["metadata_http"], http)
                self.assertEqual(report["packages"][1]["readiness"], READY)
                self.assertNotIn(NAMES[0] + VERSION_SUFFIX, calls)

    def test_network_and_invalid_json(self):
        for value in (OSError("offline"), b"not-json", b"x" * 2_000_001, []):
            with self.subTest(kind=type(value).__name__):
                status, report, calls = self.invoke({NAMES[0]: value})
                self.assertEqual(status, 1)
                self.assertEqual(report["packages"][0]["readiness"], "blocked")
                self.assertNotIn(NAMES[0] + VERSION_SUFFIX, calls)

    def test_public_internal_unknown_visibility_blocks(self):
        for visibility in ("public", "internal", None, "unknown"):
            with self.subTest(visibility=visibility):
                status, report, _ = self.invoke({NAMES[0]: metadata(NAMES[0], visibility)})
                self.assertEqual(status, 1)
                self.assertFalse(report["both_private_and_readable"])

    def test_identity_mismatch_blocks(self):
        for changes in ({"name": "wrong"}, {"package_type": "npm"}, {"owner": {"login": "other"}}, {"id": "100"}):
            with self.subTest(changes=changes):
                body = metadata(NAMES[0])
                body.update(changes)
                status, report, calls = self.invoke({NAMES[0]: body})
                self.assertEqual(status, 1)
                self.assertEqual(report["packages"][0]["metadata_error"], "unexpected_package_identity")
                self.assertNotIn(NAMES[0] + VERSION_SUFFIX, calls)

    def test_versions_errors_and_bad_shape_block(self):
        for value in (401, 403, 404, 429, 500, OSError("offline"), b"bad", {"unexpected": []}):
            with self.subTest(kind=str(value)[:40]):
                status, report, _ = self.invoke({NAMES[0] + VERSION_SUFFIX: value})
                self.assertEqual(status, 1)
                self.assertEqual(report["packages"][0]["readiness"], "blocked")
                self.assertEqual(report["packages"][0]["repository_token_read"], "unconfirmed")

    def test_link_is_not_write_authority(self):
        for linked in (None, {"full_name": "other/repo"}):
            with self.subTest(linked=linked):
                status, report, _ = self.invoke({NAMES[0]: metadata(NAMES[0], repository=linked)})
                self.assertEqual(status, 0)
                self.assertEqual(report["packages"][0]["repository_actions_write"], "not_tested")
                self.assertNotEqual(report["packages"][0]["linked_to_expected_repo"], True)

    def test_read_only_workflow_contract(self):
        wf = WORKFLOW.read_text()
        self.assertIn("packages: read", wf)
        self.assertNotIn("packages: write", wf)
        self.assertNotIn("docker push", wf)
        self.assertIn('method="GET"', wf)
        self.assertIn("class NoRedirect", wf)
        self.assertIn("sys.exit(1)", wf)
        # Explicitly exercise the no-redirect handler from the actual source.
        import ast
        node = next(n for n in ast.parse(code()).body if isinstance(n, ast.ClassDef) and n.name == "NoRedirect")
        import urllib.request
        scope = {"urllib": urllib}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<handler>", "exec"), scope)
        self.assertIsNone(scope["NoRedirect"]().redirect_request(None, None, 302, "", {}, "https://other.invalid"))
