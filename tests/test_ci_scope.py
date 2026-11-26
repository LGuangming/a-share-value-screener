import importlib.util
import pathlib
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ci_scope", pathlib.Path(__file__).with_name("ci_scope.py"))
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class ScopeTests(unittest.TestCase):
    def test_docs_and_empty(self):
        for paths in ([], ["README.md", "docs/PRODUCTION_IMAGES.md"]):
            self.assertFalse(any(scope.classify(paths).values()))

    def test_css_and_web(self):
        self.assertEqual(scope.classify(["app/theme-orange.css"])["quick_web"], True)
        result = scope.classify(["app/theme-orange.css", "components/example.tsx"])
        self.assertTrue(result["web"])
        self.assertFalse(result["quick_web"])
        self.assertFalse(result["docker"])

    def test_backend_shared_contracts(self):
        for path in ("backend/value_screener/cli.py", "lib/db.ts", "strategies/catalog.json", "proxy.ts"):
            result = scope.classify([path])
            self.assertTrue(all(result[k] for k in ("python", "integration", "web")))
            self.assertFalse(result["docker"])

    def test_full_and_unknown(self):
        for path in ("database/schema.sql", "Dockerfile.worker", "compose.yaml", "package-lock.json",
                     "backend/requirements.txt", "deploy/base-images.lock", ".github/workflows/ci.yml",
                     "tests/fixture.py", "docs/legacy-ghcr-workflows/README.md", "new-module/file"):
            self.assertEqual(scope.classify([path]), scope.FULL)
        self.assertEqual(scope.classify(["README.md"], force=True), scope.FULL)
        self.assertEqual(scope.classify(None), scope.FULL)

    def test_diff_uses_event_base_and_no_renames(self):
        with patch.object(scope.subprocess, "check_output", return_value=b"app/a.css\0backend/a.py\0") as call:
            self.assertEqual(scope.changed_paths({"pull_request": {"base": {"sha": "base"}}}, "pull_request", "head"),
                             ["app/a.css", "backend/a.py"])
            self.assertEqual(call.call_args.args[0], ["git", "diff", "--name-only", "--no-renames", "-z", "base", "head", "--"])
        with patch.object(scope.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "git")):
            self.assertIsNone(scope.changed_paths({"before": "base"}, "push", "head"))
        self.assertIsNone(scope.changed_paths({"before": "0"*40}, "push", "head"))

if __name__ == "__main__":
    unittest.main()
