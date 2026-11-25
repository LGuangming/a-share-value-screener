import os
from unittest import TestCase
from unittest.mock import patch
from shared_target import shared, hba_fragment, hba_guard, HBA_RULES, ROLES, validate_record
from postgres import check_env

class SharedGuards(TestCase):
    def test_default_5432_target_denied(self):
        with patch.dict(os.environ,{"PGHOST":"127.0.0.1","PGPORT":"5432","PGDATABASE":"ashare_migration"},clear=True):
            with self.assertRaises(ValueError): check_env(target=True)
    def test_shared_protected_db_and_wrong_role_denied(self):
        for db in ("ashare","ashare_test","ashare_migration"):
            with patch.dict(os.environ,{"ASHARE_MIGRATION_MODE":"shared","PGHOST":"127.0.0.1","PGPORT":"5432","PGDATABASE":db,"PGUSER":"postgres"},clear=True):
                with self.assertRaises(ValueError): check_env(target=True)
                if db!="ashare_migration":
                    with self.assertRaises(ValueError): check_env(live=True)
    def test_isolated_target_still_allowed(self):
        with patch.dict(os.environ,{"PGHOST":"127.0.0.1","PGPORT":"55432","PGDATABASE":"ashare_migration"},clear=True):
            check_env(target=True)
    def test_hba_exact_first_rules(self):
        rows=[[*r,None] for r in HBA_RULES]
        hba_guard(rows)
        self.assertEqual(len(hba_fragment().splitlines()),4)
        for bad in (rows[1:],list(reversed(rows)),[["host",["all"],["all"],"all","trust",None]]+rows[:3]):
            with self.assertRaises(ValueError): hba_guard(bad)
    def test_record_schema(self):
        doc={"container_id":"a"*64,"system_identifier":"123456789","database_oid":123,"owner":ROLES[0],"role_oids":dict(zip(ROLES,(10,11,12)))}
        self.assertEqual(validate_record(doc),doc)
        for bad in ({**doc,"owner":"postgres"},{**doc,"role_oids":dict(zip(ROLES,(10,10,12)))},{**doc,"database_oid":0}):
            with self.assertRaises(ValueError): validate_record(bad)
