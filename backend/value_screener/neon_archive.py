"""Private Neon Object Storage archive and recovery for backtest Parquet.

Neon Object Storage is S3-compatible, branch-scoped, and path-style. This module
does not create resources or grant public access. It fails closed unless the
operator confirmed the bucket is private in Neon Console.

Never print any credential, bucket URL, signed request or object contents.
Only archive a fully validated local year. Publish the manifest LAST, after
every month was downloaded again and checked against its SHA-256.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .archive_paths import RAW_PREFIX as PREFIX
NEON_HOST = re.compile(
    r"^br-[a-z0-9-]+\.storage\.c-[0-9]+\.([a-z0-9-]+)\.aws\.neon\.tech$"
)
KEY_PATTERN = re.compile(r"^daily_prices/year=(20[0-9]{2})/month=(0[1-9]|1[0-2])\.parquet$")
CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StorageConfig:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str

    @classmethod
    def from_environment(cls) -> "StorageConfig":
        fields = {
            "NEON_STORAGE_ENDPOINT": os.environ.get("NEON_STORAGE_ENDPOINT"),
            "NEON_STORAGE_BUCKET": os.environ.get("NEON_STORAGE_BUCKET"),
            "NEON_STORAGE_ACCESS_KEY_ID": os.environ.get("NEON_STORAGE_ACCESS_KEY_ID"),
            "NEON_STORAGE_SECRET_ACCESS_KEY": os.environ.get("NEON_STORAGE_SECRET_ACCESS_KEY"),
        }
        missing = [key for key, value in fields.items() if not value]
        if missing:
            raise RuntimeError("Neon storage settings missing: " + ", ".join(missing))
        if os.getenv("NEON_STORAGE_PRIVATE_CONFIRMED") != "true":
            raise RuntimeError(
                "NEON_STORAGE_PRIVATE_CONFIRMED must be true only after verifying "
                "the bucket is private in the Neon Console"
            )
        endpoint = fields["NEON_STORAGE_ENDPOINT"].strip().rstrip("/")
        url = urlsplit(endpoint)
        match = NEON_HOST.fullmatch(url.hostname or "")
        if (url.scheme != "https" or not match or url.port is not None
                or url.username is not None or url.password is not None
                or url.path not in ("", "/") or url.query or url.fragment):
            raise RuntimeError("NEON_STORAGE_ENDPOINT must be a branch-scoped HTTPS Neon S3 URL")
        bucket = fields["NEON_STORAGE_BUCKET"].strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",bucket):
            raise RuntimeError("Invalid Neon private bucket name")
        region = os.getenv("NEON_STORAGE_REGION") or match.group(1)
        if not re.fullmatch(r"[a-z0-9-]+",region):
            raise RuntimeError("Invalid Neon storage region")
        return cls(endpoint,bucket,fields["NEON_STORAGE_ACCESS_KEY_ID"],
                   fields["NEON_STORAGE_SECRET_ACCESS_KEY"],region)


def s3_client(config: StorageConfig):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style":"path"},
            retries={"mode":"standard","max_attempts":3},
            connect_timeout=15,read_timeout=90,
        ),
    )


def sha256_stream(reader, destination: Path | None = None) -> tuple[str,int]:
    digest=hashlib.sha256()
    size=0
    handle=None
    try:
        if destination is not None:
            destination.parent.mkdir(parents=True,exist_ok=True)
            handle=destination.open("wb")
        while True:
            chunk=reader.read(1024*1024)
            if not chunk:
                break
            size+=len(chunk)
            digest.update(chunk)
            if handle:
                handle.write(chunk)
    finally:
        if handle is not None:
            handle.close()
    return digest.hexdigest(),size


def local_manifest(root: Path,year:int) -> tuple[dict,bytes]:
    path=root/"manifest.json"
    if not path.is_file():
        raise RuntimeError("Local historical manifest is missing; export first")
    raw=path.read_bytes()
    manifest=json.loads(raw)
    validate_manifest(manifest,year)
    for part in manifest["partition_files"]:
        item=root/part["path"]
        if not item.is_file():
            raise RuntimeError("Missing local Parquet file: "+part["path"])
        with item.open("rb") as fp:
            digest,size=sha256_stream(fp)
        if size!=part["bytes"] or digest!=part["sha256"]:
            raise RuntimeError("Local Parquet checksum mismatch: "+part["path"])
    return manifest,raw


def validate_manifest(manifest:dict,year:int) -> None:
    if not isinstance(manifest,dict) or manifest.get("format_version")!=1:
        raise RuntimeError("Unsupported historical manifest")
    if (manifest.get("dataset")!="a_share_daily" or
            manifest.get("adjustment")!="unadjusted" or
            manifest.get("year")!=year):
        raise RuntimeError("Wrong dataset/year or adjustment type")
    parts=manifest.get("partition_files")
    if not isinstance(parts,list) or len(parts)!=12:
        raise RuntimeError("Completed year requires 12 monthly Parquet partitions")
    observed=set()
    count=0
    bytes_total=0
    sessions=0
    for part in parts:
        if not isinstance(part,dict):
            raise RuntimeError("Invalid partition metadata")
        relative=part.get("path")
        if not isinstance(relative,str):
            raise RuntimeError("Invalid partition path")
        match=KEY_PATTERN.fullmatch(relative)
        if not match or int(match.group(1))!=year:
            raise RuntimeError("Unexpected partition path")
        if match.group(2) in observed:
            raise RuntimeError("Duplicate month in manifest")
        observed.add(match.group(2))
        if (not isinstance(part.get("sha256"),str) or
                not CHECKSUM_PATTERN.fullmatch(part["sha256"])):
            raise RuntimeError("Invalid partition checksum")
        for key in ("bytes","rows","sessions"):
            if type(part.get(key)) is not int or part[key]<=0:
                raise RuntimeError("Invalid partition "+key)
        count+=part["rows"]
        bytes_total+=part["bytes"]
        sessions+=part["sessions"]
    if (observed!={f"{x:02d}" for x in range(1,13)} or
            count!=manifest.get("rows") or
            bytes_total!=manifest.get("parquet_bytes") or
            sessions!=manifest.get("sessions") or
            manifest.get("expected_sessions")!=sessions or
            sessions<180):
        raise RuntimeError("Partition totals or coverage do not reconcile")


def verify_object(client, bucket:str, key:str, expected_size:int,
                  expected_sha:str, dest:Path | None = None) -> None:
    head=client.head_object(Bucket=bucket,Key=key)
    if int(head["ContentLength"])!=expected_size:
        raise RuntimeError("Neon object size mismatch for "+key)
    remote_metadata=head.get("Metadata") or {}
    if remote_metadata.get("sha256")!=expected_sha:
        raise RuntimeError("Neon object metadata SHA-256 mismatch for "+key)
    response=client.get_object(Bucket=bucket,Key=key)
    body=response["Body"]
    try:
        digest,size=sha256_stream(body,dest)
    finally:
        body.close()
    if digest!=expected_sha or size!=expected_size:
        if dest:
            dest.unlink(missing_ok=True)
        raise RuntimeError("Neon object downloaded SHA-256 mismatch for "+key)


def preflight(client,bucket:str) -> None:
    """Actual private S3 write/read/delete round-trip, never a fake status check."""
    payload=b"neon-private-backtest-storage-healthcheck-v1"
    key=f"{PREFIX}/_healthcheck/{uuid4().hex}"
    checksum=hashlib.sha256(payload).hexdigest()
    try:
        client.put_object(
            Bucket=bucket,Key=key,Body=payload,
            Metadata={"sha256":checksum},ContentType="application/octet-stream"
        )
        verify_object(client,bucket,key,len(payload),checksum)
        print("NEON_STORAGE_PREFLIGHT read_write_delete_verified",flush=True)
    finally:
        # Permissions must include write/delete; failures are intentionally fatal.
        client.delete_object(Bucket=bucket,Key=key)


def _manifest_key(year:int) -> str:
    return f"{PREFIX}/daily_prices/year={year}/manifest.json"


def _remote_manifest(client,bucket:str,year:int) -> tuple[dict,bytes] | None:
    key=_manifest_key(year)
    try:
        response=client.get_object(Bucket=bucket,Key=key)
    except Exception as exc:
        # Real botocore errors expose .response. KeyError is only for
        # the dependency-free in-memory S3 fake used by unit tests.
        code=str(getattr(exc,"response",{}).get("Error",{}).get("Code",""))
        if code in ("NoSuchKey","404","NotFound") or isinstance(exc,KeyError):
            return None
        raise
    body=response["Body"]
    try:
        raw=body.read()
    finally:
        body.close()
    doc=json.loads(raw)
    validate_manifest(doc,year)
    return doc,raw


def verify_archive(client,bucket:str,year:int,destination:Path | None = None) -> dict:
    remote=_remote_manifest(client,bucket,year)
    if remote is None:
        raise RuntimeError(f"Neon archive for year {year} does not exist")
    manifest,raw=remote
    if destination is not None:
        destination.mkdir(parents=True,exist_ok=True)
    for part in manifest["partition_files"]:
        key=f"{PREFIX}/{part['path']}"
        dest=(destination/part["path"]) if destination is not None else None
        verify_object(client,bucket,key,part["bytes"],part["sha256"],dest)
    # Manifest stored last, so readers only see complete-year archives.
    if destination is not None:
        (destination/"manifest.json").write_bytes(raw)
    print("NEON_STORAGE_VERIFY "+json.dumps({
        "year":year,"status":"verified_download_sha256",
        "sessions":manifest["sessions"],"rows":manifest["rows"],
        "parquet_bytes":manifest["parquet_bytes"],
        "partitions":len(manifest["partition_files"]),
    },sort_keys=True),flush=True)
    return manifest


def upload_archive(client,bucket:str,year:int,root:Path) -> dict:
    manifest,raw=local_manifest(root,year)
    existing=_remote_manifest(client,bucket,year)
    if existing:
        if existing[0]!=manifest:
            raise RuntimeError(
                "This year already has a DIFFERENT published manifest. "
                "Refusing to overwrite a versioned dataset"
            )
        print("NEON_STORAGE_UPLOAD archive_manifest_already_published",flush=True)
        return verify_archive(client,bucket,year)
    for part in manifest["partition_files"]:
        key=f"{PREFIX}/{part['path']}"
        try:
            verify_object(client,bucket,key,part["bytes"],part["sha256"])
            print("NEON_STORAGE_UPLOAD reused_valid_part="+part["path"],flush=True)
            continue
        except RuntimeError:
            # Previous orphan with an invalid checksum. No published manifest
            # exists yet, so this partition may be repaired.
            pass
        except Exception as exc:
            code=str(getattr(exc,"response",{}).get("Error",{}).get("Code",""))
            if code not in ("NoSuchKey","404","NotFound") and not isinstance(exc,KeyError):
                raise
        client.upload_file(
            str(root/part["path"]),bucket,key,
            ExtraArgs={"Metadata":{"sha256":part["sha256"]},
                       "ContentType":"application/octet-stream"},
        )
        verify_object(client,bucket,key,part["bytes"],part["sha256"])
        print("NEON_STORAGE_UPLOAD verified_part="+part["path"],flush=True)
    key=_manifest_key(year)
    checksum=hashlib.sha256(raw).hexdigest()
    client.put_object(
        Bucket=bucket,Key=key,Body=raw,ContentType="application/json",
        Metadata={"sha256":checksum},
    )
    verify_object(client,bucket,key,len(raw),checksum)
    return verify_archive(client,bucket,year)


def main():
    parser=argparse.ArgumentParser(description="Neon private Object Storage archive access")
    parser.add_argument("action",choices=("check","upload","verify","restore"))
    parser.add_argument("--year",type=int,default=2025)
    parser.add_argument("--root",default="artifacts/history-2025")
    args=parser.parse_args()
    if not 2010<=args.year<=2099:
        parser.error("Invalid completed historical year")
    config=StorageConfig.from_environment()
    client=s3_client(config)
    if args.action=="check":
        preflight(client,config.bucket)
        return
    if args.action=="upload":
        preflight(client,config.bucket)
        result=upload_archive(client,config.bucket,args.year,Path(args.root))
        print("NEON_STORAGE_STATUS uploaded_and_verified year="+str(result["year"]),flush=True)
    elif args.action=="verify":
        verify_archive(client,config.bucket,args.year)
    else:
        verify_archive(client,config.bucket,args.year,Path(args.root))
        print("NEON_STORAGE_STATUS restored_and_verified year="+str(args.year),flush=True)


if __name__=="__main__":
    main()
