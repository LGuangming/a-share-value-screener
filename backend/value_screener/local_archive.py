"""Private filesystem implementation of the small S3 contract used by the pipeline.

The archive directory is a trusted, dedicated Linux filesystem. Data files remain
at their original keys. Metadata is content-addressed and published *before*
atomic data replacement so an interrupted write never certifies partial bytes.
No automatic trust of unindexed files; source export must carry verified metadata.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .archive_paths import RAW_PREFIX

SHA = re.compile(r"[a-f0-9]{64}")
READER = re.compile(re.escape(RAW_PREFIX) + r"/(?:daily_prices|adj_factor)/year=20[0-9]{2}/month=(?:0[1-9]|1[0-2])/reader/sha256=[a-f0-9]{64}\.parquet")
HEALTH = re.compile(re.escape(RAW_PREFIX) + r"/_healthcheck/[a-f0-9]{32}")
LOCAL_BUCKET = "local-private-archive"
CHUNK = 1024 * 1024


class MissingObject(FileNotFoundError):
    response = {"Error": {"Code": "NoSuchKey"}}


def archive_root(value):
    if not value or not os.path.isabs(value):
        raise RuntimeError("ARCHIVE_LOCAL_ROOT must be an existing absolute directory")
    root = Path(value)
    if root == Path("/") or root.resolve(strict=True) != root or not root.is_dir():
        raise RuntimeError("Invalid canonical archive root")
    return root


def safe_path(root, relative, create_parents=False):
    parts = relative.split("/")
    if any(not p or p in (".", "..") or "\\" in p or any(ord(c)<32 or ord(c)==127 for c in p) for p in parts):
        raise RuntimeError("Invalid archive path")
    path = root
    for i, part in enumerate(parts):
        path = path / part
        try:
            st = path.lstat()
        except FileNotFoundError:
            if create_parents and i < len(parts)-1:
                path.mkdir(mode=0o750, exist_ok=True)
                st = path.lstat()
            else:
                continue
        if stat.S_ISLNK(st.st_mode):
            raise RuntimeError("Archive symlink rejected")
        if i < len(parts)-1 and not stat.S_ISDIR(st.st_mode):
            raise RuntimeError("Invalid archive parent")
        if i == len(parts)-1 and not stat.S_ISREG(st.st_mode):
            raise RuntimeError("Invalid archive file")
    return path


def ensure_dir(root, relative):
    # Use a sentinel path to validate/create every directory, never follow symlinks.
    return safe_path(root, relative+"/.sentinel", True).parent


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def open_regular(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as exc:
        raise MissingObject("Archive object missing") from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError("Invalid archive file")
    return os.fdopen(fd, "rb")


@contextmanager
def archive_lock(root, name, blocking=False):
    if name not in ("pipeline", "writer"):
        raise RuntimeError("Invalid archive lock")
    path = safe_path(root, ".archive_meta/"+name+".lock", True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("Invalid archive lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RuntimeError("Another local archive task is running") from exc
        yield
    finally:
        os.close(fd)


def identity(st):
    return '"' + hashlib.sha256(f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest() + '"'


def modified(st):
    return datetime.fromtimestamp(st.st_mtime, timezone.utc)


class LocalArchive:
    def __init__(self, root):
        self.root = archive_root(root)

    def _path(self, bucket, key, parents=False):
        if bucket != LOCAL_BUCKET or not key.startswith(RAW_PREFIX+"/"):
            raise RuntimeError("Invalid archive bucket or key")
        return safe_path(self.root, key, parents)

    def _meta(self, key, sha, parents=False):
        if not SHA.fullmatch(sha):
            raise RuntimeError("Invalid archive checksum")
        digest = hashlib.sha256(key.encode()).hexdigest()
        return safe_path(self.root, f".archive_meta/objects/{digest[:2]}/{digest}/{sha}.json", parents)

    def _read_verified(self, bucket, key):
        path = self._path(bucket, key)
        fp = open_regular(path)
        try:
            before = os.fstat(fp.fileno())
            digest = hashlib.sha256()
            for chunk in iter(lambda: fp.read(CHUNK), b""):
                digest.update(chunk)
            sha = digest.hexdigest()
            after = os.fstat(fp.fileno())
            if identity(before) != identity(after):
                raise RuntimeError("Archive changed during verification")
            try:
                with open_regular(self._meta(key, sha)) as mf:
                    doc = json.loads(mf.read(65537))
            except MissingObject as exc:
                raise RuntimeError("Archive metadata missing or checksum mismatch") from exc
            if (doc.get("key") != key or doc.get("size") != before.st_size
                    or doc.get("sha256") != sha or not isinstance(doc.get("content_type"), str)):
                raise RuntimeError("Archive metadata mismatch")
            fp.seek(0)
            return fp, {"ContentLength": before.st_size, "Metadata": {"sha256": sha},
                        "ContentType": doc["content_type"], "ETag": identity(before),
                        "LastModified": modified(before)}
        except BaseException:
            fp.close()
            raise

    def get_object(self, Bucket, Key):
        fp, head = self._read_verified(Bucket, Key)
        return {**head, "Body": fp}

    def head_object(self, Bucket, Key):
        fp, head = self._read_verified(Bucket, Key)
        fp.close()
        return head

    def _atomic_meta(self, key, sha, size, content_type):
        path = self._meta(key, sha, True)
        doc = {"key": key, "sha256": sha, "size": size, "content_type": content_type}
        if path.exists():
            with open_regular(path) as fp:
                if json.loads(fp.read(65537)) != doc:
                    raise RuntimeError("Conflicting local archive metadata")
            return
        fd, tmp = tempfile.mkstemp(prefix=".meta-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as fp:
                fp.write(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, path)
            fsync_dir(path.parent)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def put_object(self, Bucket, Key, Body, Metadata=None, ContentType=None, ContentLength=None):
        sha = (Metadata or {}).get("sha256", "")
        if not SHA.fullmatch(sha):
            raise RuntimeError("Expected archive SHA-256 metadata")
        content_type = ContentType or "application/octet-stream"
        if not isinstance(content_type, str) or len(content_type)>200:
            raise RuntimeError("Invalid content type")
        with archive_lock(self.root, "writer", True):
            dest = self._path(Bucket, Key, True)
            staging = ensure_dir(self.root, ".archive_meta/staging")
            fd, tmp = tempfile.mkstemp(prefix="object-", dir=staging)
            try:
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as fp:
                    if isinstance(Body, (bytes, bytearray, memoryview)):
                        chunks = (Body,)
                    else:
                        chunks = iter(lambda: Body.read(CHUNK), b"")
                    for chunk in chunks:
                        fp.write(chunk)
                        size += len(chunk)
                        digest.update(chunk)
                    if digest.hexdigest()!=sha or (ContentLength is not None and size!=ContentLength):
                        raise RuntimeError("Archive write checksum/size mismatch")
                    os.fchmod(fp.fileno(), 0o640)
                    fp.flush()
                    os.fsync(fp.fileno())
                if dest.exists():
                    old = self.head_object(Bucket, Key)
                    if old["Metadata"]["sha256"] == sha:
                        if old["ContentType"] != content_type:
                            raise RuntimeError("Conflicting archive content type")
                        return {"ETag": old["ETag"]}
                    # Published binary objects are immutable; repair is an
                    # explicit operator operation, not an implicit overwrite.
                    if not Key.endswith(".json"):
                        raise RuntimeError("Refusing to replace immutable archive object")
                self._atomic_meta(Key, sha, size, content_type)
                os.replace(tmp, dest)
                fsync_dir(dest.parent)
                fsync_dir(staging)
                return {"ETag": identity(dest.stat())}
            finally:
                Path(tmp).unlink(missing_ok=True)

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        with open(Filename, "rb") as fp:
            return self.put_object(Bucket, Key, fp, ContentLength=os.fstat(fp.fileno()).st_size,
                                   **(ExtraArgs or {}))

    def _inventory(self, bucket, prefix):
        if bucket != LOCAL_BUCKET or (prefix!=RAW_PREFIX and not prefix.startswith(RAW_PREFIX+"/")):
            raise RuntimeError("Invalid archive listing prefix")
        if prefix.endswith("/"):
            prefix.rstrip("/")
        # Validate prefix, including partial last component. Walk only canonical
        # namespace; metadata/staging is deliberately excluded from inventory.
        safe_path(self.root, prefix.rstrip("/")+"/.sentinel")
        start = self.root / RAW_PREFIX
        if not start.exists():
            return []
        if start.is_symlink() or not start.is_dir():
            raise RuntimeError("Invalid archive namespace")
        out = []
        def walk(folder):
            for entry in sorted(os.scandir(folder), key=lambda e:e.name):
                key = Path(entry.path).relative_to(self.root).as_posix()
                if not (key.startswith(prefix) or prefix.startswith(key+"/") or prefix==key):
                    continue
                if entry.is_symlink():
                    raise RuntimeError("Archive symlink rejected")
                if entry.is_dir(follow_symlinks=False):
                    walk(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    if key.startswith(prefix):
                        st = entry.stat(follow_symlinks=False)
                        out.append({"Key":key,"Size":st.st_size,"ETag":identity(st),"LastModified":modified(st)})
                else:
                    raise RuntimeError("Invalid archive entry")
        walk(start)
        return out

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None, Delimiter=None):
        if not isinstance(MaxKeys,int) or not 1<=MaxKeys<=1000 or Delimiter not in (None,"/"):
            raise RuntimeError("Invalid local archive listing options")
        items = self._inventory(Bucket, Prefix)
        grouped = {}
        for row in items:
            suffix = row["Key"][len(Prefix):]
            if Delimiter and "/" in suffix:
                k = Prefix+suffix.split("/",1)[0]+"/"
                grouped[k] = ("prefix", {"Prefix":k})
            else:
                grouped[row["Key"]] = ("object", row)
        keys = sorted(grouped)
        if ContinuationToken is not None:
            if not isinstance(ContinuationToken,str) or not ContinuationToken.startswith(Prefix):
                raise RuntimeError("Invalid archive continuation token")
            keys = [k for k in keys if k>ContinuationToken]
        selected = keys[:MaxKeys]
        result = {"Contents":[grouped[k][1] for k in selected if grouped[k][0]=="object"],
                  "CommonPrefixes":[grouped[k][1] for k in selected if grouped[k][0]=="prefix"],
                  "IsTruncated":len(keys)>len(selected),"KeyCount":len(selected)}
        if result["IsTruncated"]:
            result["NextContinuationToken"] = selected[-1]
        return result

    def get_paginator(self, operation):
        if operation!="list_objects_v2":
            raise RuntimeError("Unsupported archive paginator")
        owner = self
        class Pager:
            def paginate(self, **kwargs):
                token = None
                while True:
                    page = owner.list_objects_v2(**kwargs, ContinuationToken=token)
                    yield page
                    if not page["IsTruncated"]:
                        break
                    token = page["NextContinuationToken"]
        return Pager()

    def delete_object(self, Bucket, Key):
        if not HEALTH.fullmatch(Key) and not (
            READER.fullmatch(Key) and os.getenv("ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY")=="true"
            and os.getenv("PIPELINE_RUN_ID")):
            raise RuntimeError("Local archive deletion not authorized")
        with archive_lock(self.root,"writer",True):
            path = self._path(Bucket, Key)
            if path.exists():
                head = self.head_object(Bucket, Key)
                path.unlink()
                fsync_dir(path.parent)
                # Only remove metadata after deleting the data; stale metadata
                # after interruption cannot certify bytes of a different hash.
                meta = self._meta(Key, head["Metadata"]["sha256"])
                meta.unlink(missing_ok=True)
                fsync_dir(meta.parent)
        return {}

    def close(self):
        pass
