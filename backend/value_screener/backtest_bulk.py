"""Bounded typed DuckDB vector inserts and structured process telemetry."""
from __future__ import annotations
import json
import resource
import time


def insert_columns(db,table,types,rows):
    # Internal callers supply fixed table names/types. Each chunk becomes a single
    # vectorized SQL statement, not one execute per row. Explicit types preserve
    # NULL-only columns and DATE precision without optional Arrow dependencies.
    if not rows:
        return
    for start in range(0,len(rows),5000):
        columns=list(zip(*rows[start:start+5000],strict=True))
        if len(columns)!=len(types):
            raise ValueError("Bulk row width mismatch")
        sql=",".join(f"unnest(?::{t}[])" for t in types)
        db.execute(f"INSERT INTO {table} SELECT {sql}",[list(c) for c in columns])


def progress(label,started,**fields):
    # Linux process high-water RSS, not container peak nor a hard memory bound.
    fields["elapsed_seconds"]=round(time.monotonic()-started,3)
    fields["process_peak_rss_kib"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(label+" "+json.dumps(fields,sort_keys=True),flush=True)
