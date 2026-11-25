"""Driver selection shared by existing Python archive commands."""
from __future__ import annotations
import os

def archive_driver():
    mode = os.getenv("ARCHIVE_STORAGE_DRIVER") or "neon"
    if mode not in ("neon", "local"):
        raise RuntimeError("Invalid ARCHIVE_STORAGE_DRIVER")
    return mode

def local_config():
    from .local_archive import archive_root, LOCAL_BUCKET
    return archive_root(os.getenv("ARCHIVE_LOCAL_ROOT")), LOCAL_BUCKET

def duckdb_connection():
    import duckdb
    if archive_driver()=="neon":
        return duckdb.connect()
    # The limit covers DuckDB-managed memory, not total Python RSS.
    config={"threads":"2","memory_limit":"512MB"}
    temp=os.getenv("DUCKDB_TEMP_DIRECTORY")
    if temp:
        if not os.path.isabs(temp):
            raise RuntimeError("DUCKDB_TEMP_DIRECTORY must be absolute")
        config["temp_directory"]=temp
    return duckdb.connect(config=config)

def require_neon_only():
    if archive_driver()!="neon":
        raise RuntimeError("Retired cloud migration/cleanup command is not supported in local mode")
