import os
import unittest
from unittest.mock import patch
from postgres import check_env
from readiness import validate
import tempfile

class Guards(unittest.TestCase):
    def test_production_target_rejected(self):
        with patch.dict(os.environ,{"PGHOST":"127.0.0.1","PGDATABASE":"ashare"},clear=True):
            with self.assertRaises(ValueError):
                check_env(target=True)
    def test_remote_without_approval_rejected(self):
        with patch.dict(os.environ,{"PGHOST":"example.invalid","PGDATABASE":"source"},clear=True):
            with self.assertRaises(ValueError):
                check_env()
    def test_pooler_rejected_even_approved(self):
        with patch.dict(os.environ,{"PGHOST":"example-pooler.invalid","PGDATABASE":"source","PGSSLMODE":"verify-full"},clear=True):
            with self.assertRaises(ValueError):
                check_env(live=True)
    def test_readiness_missing_fails(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                validate(root)
