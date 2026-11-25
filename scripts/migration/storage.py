"""Read-only S3 inventory and verified download; no put/delete calls."""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime, timezone
import boto3
from botocore.config import Config
from common import digest, load, main_guard, write_json

def same_modified(listed, received):
    """Compare LIST timestamps with HTTP Last-Modified without losing LIST evidence.

    HTTP dates have second precision. When GET supplies fractional precision,
    retain exact comparison instead of discarding the extra evidence.
    """
    expected = datetime.fromisoformat(listed)
    if not isinstance(received, datetime):
        raise ValueError("invalid GET LastModified")
    if expected.utcoffset() is None or received.utcoffset() is None:
        raise ValueError("timezone required for LastModified")
    expected = expected.astimezone(timezone.utc)
    received = received.astimezone(timezone.utc)
    if received.microsecond == 0:
        expected = expected.replace(microsecond=0)
    return expected == received

def safe_key(key):
    parts=key.split("/")
    if not key or any(p in ("",".","..") for p in parts) or "\\" in key or any(ord(c)<32 or ord(c)==127 for c in key):
        raise ValueError("unsafe or non-file object key")
    return parts

def safe_path(root,bucket,key):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",bucket) or ".." in bucket:
        raise ValueError("invalid bucket")
    path=root
    for part in [bucket,*safe_key(key)]:
        path=path/part
        if path.is_symlink():
            raise ValueError("symlink not permitted")
    return path

def inventory(client, expected):
    # Full endpoint enumeration is required: a scoped credential that cannot
    # list buckets fails closed rather than silently claiming complete coverage.
    discovered=[]
    for page in client.get_paginator("list_buckets").paginate():
        discovered.extend(b["Name"] for b in page["Buckets"])
    if not expected or sorted(discovered)!=sorted(set(expected)):
        raise ValueError("bucket scope differs from independently approved list")
    objects=[]
    prefixes={}
    for b in sorted(discovered):
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=b):
            for o in page.get("Contents",[]):
                key=o["Key"]
                safe_path(Path("."),b,key)
                item={"bucket":b,"key":key,"size":o["Size"],"etag":o["ETag"],"modified":o["LastModified"].isoformat()}
                objects.append(item)
                prefix=b+"/"+"/".join(key.split("/")[:3])
                entry=prefixes.setdefault(prefix,{"objects":0,"bytes":0})
                entry["objects"]+=1
                entry["bytes"]+=item["size"]
    objects.sort(key=lambda x:(x["bucket"],x["key"]))
    # Reject path collisions such as objects 'a' and 'a/b'.
    keys={(x["bucket"],x["key"]) for x in objects}
    if len(keys)!=len(objects):
        raise ValueError("duplicate listing")
    for b,k in keys:
        parts=safe_key(k)
        if any((b,"/".join(parts[:i])) in keys for i in range(1,len(parts))):
            raise ValueError("file/directory collision")
    return {"captured_at":datetime.now(timezone.utc).isoformat(),"buckets":sorted(discovered),"objects":objects,"total_bytes":sum(x["size"] for x in objects),"prefixes":prefixes}

def sync(client, manifest, root, previous=None, workers=1):
    if workers not in (1,2,4):
        raise ValueError("workers must be 1, 2 or 4")
    root=Path(root)
    (root/"verify-storage.json").unlink(missing_ok=True)
    (root/"verify-archive.json").unlink(missing_ok=True)
    data=root/"files"
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    if root.is_symlink() or data.is_symlink():
        raise ValueError("symlink root")
    statefile=root/"checkpoint.json"
    journal=root/"checkpoint.jsonl"
    if statefile.is_symlink() or journal.is_symlink():
        raise ValueError("symlink evidence")
    state=load(statefile) if statefile.exists() else {}
    if journal.exists():
        # A torn tail is not trusted: fail closed, retain evidence for review.
        with journal.open(encoding="utf-8") as f:
            for line in f:
                record=json.loads(line)
                state[record["id"]]=record["receipt"]
    prior=load(Path(previous)/"checkpoint.json") if previous else {}
    todo=[]
    for obj in manifest["objects"]:
        b,k=obj["bucket"],obj["key"]
        ident=b+"/"+k
        path=safe_path(data,b,k)
        old=state.get(ident)
        if path.exists():
            if old and old["object"]==obj and path.is_file() and path.stat().st_size==obj["size"] and digest(path)==old["sha256"]:
                continue
            raise ValueError("existing file is unverified or changed; use new generation")
        todo.append((obj,ident,path))
    if shutil.disk_usage(data).free < sum(x[0]["size"] for x in todo)+64*1024*1024:
        raise OSError("insufficient disk")
    def prepare(task):
        obj,ident,path=task
        bucket,key=obj["bucket"],obj["key"]
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,temp=tempfile.mkstemp(prefix=".download-",dir=path.parent)
        try:
            if previous and prior.get(ident,{}).get("object")==obj:
                src=safe_path(Path(previous)/"files",bucket,key)
                expected=prior[ident]["sha256"]
                if not src.is_file() or src.stat().st_size!=obj["size"] or digest(src)!=expected:
                    raise ValueError("previous generation corrupt")
                os.close(fd)
                fd=None
                shutil.copyfile(src,temp)
                if digest(temp)!=expected:
                    raise ValueError("copy integrity mismatch")
                return task,temp,expected
            response=client.get_object(Bucket=bucket,Key=key,IfMatch=obj["etag"])
            body=response["Body"]
            try:
                h=hashlib.sha256()
                size=0
                with os.fdopen(fd,"wb") as f:
                    fd=None
                    for chunk in body.iter_chunks(chunk_size=1024*1024):
                        f.write(chunk)
                        h.update(chunk)
                        size+=len(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                if size!=obj["size"] or response.get("ETag")!=obj["etag"] or not same_modified(obj["modified"], response["LastModified"]):
                    raise ValueError("source changed or size mismatch")
                claimed=response.get("Metadata",{}).get("sha256")
                if claimed and claimed.lower()!=h.hexdigest():
                    raise ValueError("source metadata SHA256 mismatch")
                return task,temp,h.hexdigest()
            finally:
                body.close()
        except BaseException:
            if fd is not None:
                os.close(fd)
            Path(temp).unlink(missing_ok=True)
            raise
    # Bounded groups, parallel reads but serialized durable receipt + publication.
    # The journal removes quadratic rewriting of the whole checkpoint per object.
    with ThreadPoolExecutor(max_workers=workers) as pool, journal.open("a",encoding="utf-8") as log:
        for offset in range(0,len(todo),workers):
            futures=[pool.submit(prepare,t) for t in todo[offset:offset+workers]]
            try:
                for future in futures:
                    (obj,ident,path),temp,sha=future.result()
                    receipt={"object":obj,"sha256":sha}
                    log.write(json.dumps({"id":ident,"receipt":receipt},ensure_ascii=False)+"\n")
                    log.flush()
                    os.fsync(log.fileno())
                    state[ident]=receipt
                    os.link(temp,path)
            finally:
                for future in futures:
                    try:
                        _,temp,_=future.result()
                        Path(temp).unlink(missing_ok=True)
                    except Exception:
                        pass
    write_json(statefile,state)
    journal.unlink(missing_ok=True)
    return verify(manifest,root)

def verify(manifest,root):
    root=Path(root)
    (root/"verify-storage.json").unlink(missing_ok=True)
    state=load(root/"checkpoint.json") if (root/"checkpoint.json").exists() else {}
    expected=set()
    total=0
    for obj in manifest["objects"]:
        ident=obj["bucket"]+"/"+obj["key"]
        expected.add(ident)
        path=safe_path(root/"files",obj["bucket"],obj["key"])
        old=state.get(ident)
        if not old or old["object"]!=obj or not path.is_file() or path.stat().st_size!=obj["size"] or digest(path)!=old["sha256"]:
            raise ValueError("local object missing or corrupt")
        total+=obj["size"]
    actual=set()
    for path in (root/"files").rglob("*"):
        if path.is_symlink():
            raise ValueError("unexpected symlink")
        if path.is_file():
            actual.add(path.relative_to(root/"files").as_posix())
    if actual!=expected or set(state)!=expected or total!=manifest["total_bytes"]:
        raise ValueError("extra or missing files; no automatic deletion")
    report={"passed":True,"objects":len(expected),"bytes":total,"manifest_sha256":digest(root/"inventory-storage.json")}
    write_json(root/"verify-storage.json",report)
    return report

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["inventory","sync","verify"])
    p.add_argument("--root",required=True)
    p.add_argument("--bucket",action="append",default=[])
    p.add_argument("--approved-live-read",action="store_true")
    p.add_argument("--previous-generation")
    p.add_argument("--workers",type=int,choices=[1,2,4],default=2)
    a=p.parse_args()
    root=Path(a.root)
    if a.action=="verify":
        verify(load(root/"inventory-storage.json"),root)
    else:
        if not a.approved_live_read:
            raise ValueError("explicit live read approval required")
        endpoint=os.environ["NEON_STORAGE_ENDPOINT"]
        if not endpoint.startswith("https://"):
            raise ValueError("TLS endpoint required")
        client=boto3.client("s3",endpoint_url=endpoint,region_name=os.environ["NEON_STORAGE_REGION"],aws_access_key_id=os.environ["NEON_STORAGE_ACCESS_KEY_ID"],aws_secret_access_key=os.environ["NEON_STORAGE_SECRET_ACCESS_KEY"],config=Config(retries={"mode":"standard","max_attempts":5},connect_timeout=10,read_timeout=60,s3={"addressing_style":"path"}))
        if a.previous_generation and Path(a.previous_generation).resolve()==root.resolve():
            raise ValueError("previous generation must differ")
        (root/"verify-storage.json").unlink(missing_ok=True)
        manifest=inventory(client,a.bucket)
        old=root/"inventory-storage.json"
        if old.exists():
            prior=load(old)
            # Append-only increments allowed; changed or deleted source keys require
            # fresh generation and review, never overwrite or delete.
            now={(x["bucket"],x["key"]):x for x in manifest["objects"]}
            if any(now.get((x["bucket"],x["key"]))!=x for x in prior["objects"]):
                raise ValueError("source changed or removed; new generation required")
        write_json(old,manifest)
        if a.action=="sync":
            sync(client,manifest,root,a.previous_generation,a.workers)
            try:
                final=inventory(client,a.bucket)
                if final["objects"]!=manifest["objects"]:
                    raise ValueError("source listing changed during download; repeat")
            except BaseException:
                (root/"verify-storage.json").unlink(missing_ok=True)
                raise
    print("storage evidence saved locally")

if __name__=="__main__":
    main_guard(main)
