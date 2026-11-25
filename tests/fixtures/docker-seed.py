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
assert (parsed.hostname,parsed.path,parsed.username)==("postgres","/ashare_test","fixture_writer")
assert os.environ["ARCHIVE_STORAGE_DRIVER"]=="local"
assert os.environ["ARCHIVE_LOCAL_ROOT"]=="/data/archive"
assert os.getuid()==10002 and os.getgid()==10010
assert not os.getenv("SITE_ACCESS_PASSWORD") and not os.getenv("SITE_SESSION_SECRET")
store=Store(url)
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
