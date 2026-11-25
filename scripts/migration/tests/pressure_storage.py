"""Disk-backed S3-shaped load fixture. No network, no cloud credentials.
Protocol compatibility is separately tested by the existing Moto suite.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import time
from datetime import datetime, timezone, date, timedelta
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from common import digest,write_json
from storage import inventory,sync
from archive_verify import inspect

BUCKET="pressure-fixture"
PREFIX="a-share-backtest/01_raw_data/"
STAMP=datetime(2026,11,24,tzinfo=timezone.utc)

class Body:
    def __init__(self,path): self.f=path.open("rb")
    def iter_chunks(self,chunk_size):
        while chunk:=self.f.read(chunk_size): yield chunk
    def close(self): self.f.close()

class Paginator:
    def __init__(self,s3,kind): self.s3,self.kind=s3,kind
    def paginate(self,**kwargs):
        if self.kind=="list_buckets":
            yield {"Buckets":[{"Name":BUCKET}]}
        else:
            rows=list(self.s3.objects.values())
            for i in range(0,len(rows),1000): yield {"Contents":rows[i:i+1000]}

class DiskS3:
    def __init__(self,root):
        self.root=root
        self.objects={}
    def add(self,key):
        p=self.root/key
        self.objects[key]={"Key":key,"Size":p.stat().st_size,"ETag":'"'+digest(p)+'"',"LastModified":STAMP}
    def get_paginator(self,kind): return Paginator(self,kind)
    def get_object(self,Bucket,Key,IfMatch):
        obj=self.objects[Key]
        if Bucket!=BUCKET or obj["ETag"]!=IfMatch: raise ValueError("fixture conditional read failed")
        time.sleep(.001) # controlled simulated per-request overhead, not network latency
        return {"Body":Body(self.root/Key),"ETag":obj["ETag"],"LastModified":obj["LastModified"]}
    def put_fixture(self,key,data):
        path=self.root/key
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        self.add(key)
    def manifest(self,key,extra):
        p=self.root/key
        self.put_fixture(key+".manifest.json",json.dumps({"object_key":key,"bytes":p.stat().st_size,"sha256":digest(p),**extra},sort_keys=True).encode())

def metrics(label,start,root,extra=None):
    size=sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    result={"phase":label,"seconds":round(time.monotonic()-start,3),"logical_file_bytes":size,"process_peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    result.update(extra or {})
    print("PRESSURE_STORAGE "+json.dumps(result,sort_keys=True),flush=True)

def build(s3,rows):
    rng=np.random.default_rng(20261125)
    n=100
    fields=["ts_code","trade_date","total_mv","circ_mv","total_share","float_share","free_share","turnover_rate","pb","pe_ttm"]
    for i in range(n):
        key=PREFIX+f"daily_basic/fixture/month={i:03d}.parquet"
        p=s3.root/key
        p.parent.mkdir(parents=True,exist_ok=True)
        codes=[f"{j%5000+1:06d}.SZ" for j in range(rows)]
        days=[date(2020,1,1)+timedelta(days=i*40+j//5000) for j in range(rows)]
        columns={"ts_code":codes,"trade_date":days}
        for field in fields[2:]: columns[field]=rng.random(rows)*1000
        pq.write_table(pa.table(columns),p,compression="zstd")
        s3.add(key)
        s3.manifest(key,{"dataset":"daily_basic","rows":rows,"fields":fields})
    for i in range(2900):
        key=PREFIX+f"historical_st/v2/fixture/raw/batch={i:05d}.json.gz"
        records=[{"ts_code":f"{i%5000+1:06d}.SZ","namechange":[],"baostock":{"status":"not_collected","rows":[]}}]
        s3.put_fixture(key,gzip.compress(json.dumps(records).encode(),mtime=0))
        s3.manifest(key,{"codes":[records[0]["ts_code"]],"first":"20200101","last":"20251231"})
    return inventory(s3,[BUCKET])

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    p.add_argument("--rows",type=int,choices=[90000,150000],required=True)
    a=p.parse_args()
    root=Path(a.root)
    root.mkdir(parents=True,exist_ok=False)
    s3=DiskS3(root/"source")
    t=time.monotonic()
    m=build(s3,a.rows)
    if len(m["objects"])!=6000 or m["total_bytes"]<450_000_000:
        raise RuntimeError("fixture undersized")
    metrics("source_build",t,s3.root,{"objects":len(m["objects"]),"source_bytes":m["total_bytes"]})
    for workers in (1,2,4):
        dest=root/f"generation-{workers}"
        write_json(dest/"inventory-storage.json",m)
        t=time.monotonic()
        result=sync(s3,m,dest,workers=workers)
        metrics("full_sync",t,dest,{"workers":workers,**result})
        if workers!=4:
            shutil.rmtree(dest) # generated runner fixture only
    old=root/"generation-4"
    for limit in ("256MB","512MB"):
        t=time.monotonic()
        r=inspect(old,limit)
        metrics("archive_verify",t,old,{"memory_limit":limit,"parquet_rows":r["parquet_rows"],"gzip_rows":r["gzip_rows"]})
    # Change one complete manifest+data pair; original remains intact.
    key=PREFIX+"historical_st/v2/fixture/raw/batch=00000.json.gz"
    old_hash=digest(old/"files"/BUCKET/key)
    s3.put_fixture(key,gzip.compress(json.dumps([{"ts_code":"000001.SZ","namechange":[],"baostock":{"status":"empty","rows":[]}}]).encode(),mtime=0))
    s3.manifest(key,{"codes":["000001.SZ"],"first":"20200101","last":"20251231"})
    updated=inventory(s3,[BUCKET])
    new=root/"increment"
    write_json(new/"inventory-storage.json",updated)
    t=time.monotonic()
    r=sync(s3,updated,new,previous=old,workers=4)
    if digest(old/"files"/BUCKET/key)!=old_hash or digest(new/"files"/BUCKET/key)==old_hash: raise RuntimeError("generation isolation failed")
    metrics("increment",t,new,{"workers":4,**r})
    inspect(new)
    print("PASS: pressure archive generation and increment",flush=True)

if __name__=="__main__": main()
