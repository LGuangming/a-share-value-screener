import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import bridge
import security_audit as audit
try:
    import duckdb
except ImportError:
    duckdb=None

def file_hash(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

class SecurityAuditTests(unittest.TestCase):
    def test_sanitizer(self):
        d={"status":audit.STATUS,"state":"completed","expected_partitions":72,
           "verified_partitions":72,"years":{str(y):{"market_rows":100,"secret":"never"} for y in range(2020,2026)},
           "path":"/private/path"}
        out=bridge.clean_report("security_audit",d)
        self.assertEqual(out["verified_partitions"],72)
        self.assertEqual(out["years"]["2020"],{"market_rows":100})
        self.assertFalse(out["formal_backtest_ready"])
        self.assertNotIn("path",out)

    @unittest.skipIf(duckdb is None,"DuckDB not present in this runtime")
    def test_actual_parquet_integrity_and_leakage(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);m=root/"m";s=root/"s";m.mkdir();s.mkdir()
            db=duckdb.connect(config={"threads":"1","memory_limit":"128MB"})
            try:
                def save_table(folder,prefix,query):
                    p=folder/"data.parquet"
                    db.execute("COPY ("+query+") TO ? (FORMAT PARQUET)",[str(p)])
                    h=file_hash(p);dest=folder/f"{prefix}-{h}.parquet";p.rename(dest)
                    return {"file":dest.name,"sha256":h,"bytes":dest.stat().st_size}
                mi=save_table(m,"market","""SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date,
                    'NORMAL' st_type, DATE '2024-12-31' st_ann_date, true st_normal_evidence,
                    true is_mainboard_code, true factor_valid, true limits_valid""")
                (m/"manifest.json").write_text(json.dumps({"version":1,"year":2025,"month":1,
                    "status":"research_data_not_backtest_ready","outputs":{"market":mi},"quality":{"rows":1}}))
                def security(certified):
                    si=save_table(s,"security",f"""SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date,
                        'NORMAL' historical_st_type,'historical_merged_not_certified' st_evidence,
                        'within_snapshot_listing_dates_not_certified' listing_evidence,
                        {str(certified).lower()} st_certified, NULL::VARCHAR namechange_source_sha256""")
                    (s/"manifest.json").write_text(json.dumps({"version":1,"status":"security_evidence_not_certified",
                        **si,"inputs":{"market_sha256":mi["sha256"],"snapshot_sha256":"a"*64,
                        "capture_day":None,"namechange_hashes":{}},"quality":{"rows":1,
                        "st_evidence_counts":{"historical_merged_not_certified":1}}}))
                    return si
                security(False)
                metrics,ok=audit.partition(db,m,s,2025,1,"a"*64)
                self.assertTrue(ok);self.assertEqual(metrics["provisional_eligible_rows"],1)
                si=security(True)
                metrics,ok=audit.partition(db,m,s,2025,1,"a"*64)
                self.assertFalse(ok);self.assertEqual(metrics["invalid_evidence_rows"],1)
                (s/si["file"]).write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError,"Checksum"):audit.partition(db,m,s,2025,1,"a"*64)
            finally:db.close()

if __name__=="__main__":unittest.main()
