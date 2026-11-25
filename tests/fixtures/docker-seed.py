"""CI-only seed using real worker image, restricted SQL writer and local adapter."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import date
from urllib.parse import urlsplit
from value_screener.neon_archive import StorageConfig,s3_client,verify_object
from value_screener.store import Store
from value_screener.archive_paths import RAW_PREFIX

url=os.environ["DATABASE_URL"]
parsed=urlsplit(url)
assert (parsed.hostname,parsed.path,parsed.username)==("postgres","/ashare_test","screener_writer")
assert os.environ["ARCHIVE_STORAGE_DRIVER"]=="local"
assert os.environ["ARCHIVE_LOCAL_ROOT"]=="/data/archive"
assert os.getuid()==10002 and os.getgid()==10010
assert not os.getenv("SITE_ACCESS_PASSWORD") and not os.getenv("SITE_SESSION_SECRET")
store=Store(url)
store.ensure_price_history_tables()
with store.connect() as conn:
    assert not conn.execute("SELECT has_schema_privilege(current_user,'public','CREATE') AS allowed").fetchone()["allowed"]
    assert conn.execute("SELECT current_user AS role").fetchone()["role"]=="screener_writer"
    assert not conn.execute("SELECT has_database_privilege(current_user,current_database(),'TEMP') AS allowed").fetchone()["allowed"]
    for table in ("stocks","daily_prices","financial_reports","financial_fetch_log","job_runs","price_history","price_history_fetches","screen_results"):
        for privilege in ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER"):
            expected = privilege in ("SELECT","INSERT","UPDATE") or (privilege=="DELETE" and table in ("daily_prices","price_history","price_history_fetches","screen_results"))
            actual = conn.execute("SELECT has_table_privilege(current_user,%s,%s) AS allowed",("public."+table,privilege)).fetchone()["allowed"]
            assert actual == expected,(table,privilege)
print("PASS: 56 M12-like table ACL assertions, writer identity, no CREATE/TEMP")
try:
    store.initialize("SELECT 1")
except RuntimeError as exc:
    assert "init-db disabled" in str(exc)
else:
    raise AssertionError("Local schema initialization was not rejected")
print("PASS: M12-like restricted writer, no runtime DDL or grants")
store.upsert_stocks([{"ts_code":"000001.SZ","name":"Docker Fixture","industry":"制造业","market":"主板","list_date":"19910403"}])
store.upsert_quotes([{"ts_code":"000001.SZ","close":11,"total_mv":100,"pb":0.5,"turnover_rate":1}],date(2025,1,3))
cfg=StorageConfig.from_environment()
storage=s3_client(cfg)
with tempfile.TemporaryDirectory(prefix="docker-fixture-") as tmp:
    subprocess.run([sys.executable,"/fixtures/generate-archive.py",tmp],check=True)
    root=Path(tmp)
    for file in sorted((root/RAW_PREFIX).rglob("*")):
        if not file.is_file():
            continue
        key=file.relative_to(root).as_posix()
        raw=file.read_bytes()
        sha=hashlib.sha256(raw).hexdigest()
        storage.put_object(Bucket=cfg.bucket,Key=key,Body=raw,Metadata={"sha256":sha},ContentType="application/json" if key.endswith(".json") else "application/octet-stream")
        verify_object(storage,cfg.bucket,key,len(raw),sha)
storage.close()
print("Docker worker fixture written and verified")
