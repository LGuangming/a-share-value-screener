"""One-time offline restore to the existing local ashare database.

No container creation, role creation, HBA edits, drop, clean or cutover.
Use a trusted source archive and an explicitly approved existing DB owner.
"""
import argparse
from contextlib import contextmanager
import sys
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import psycopg
from common import digest, load, write_json
from postgres import inventory, readonly, json_value, SYSTEM
from permissions import grant, policy, verify, role_guard

TARGET="ashare"
IMAGE="postgres@sha256:0115267ab5a509022df9319c77be365cef335adb71556c75f60f5360ee247e71"
DOCKER=["/usr/bin/docker","--host","unix:///var/run/docker.sock"]
DOCKER_ENV={"PATH":"/usr/bin:/bin","HOME":"/root","LANG":"C","LC_ALL":"C"}
TABLES={"stocks","daily_prices","price_history","price_history_fetches","financial_reports","financial_fetch_log","job_runs","screen_results"}
POLICY=Path(__file__).resolve().parent/"tests/production-policy.json"

class DirectError(ValueError):
    """Fixed code only: do not print database errors or connection details."""

@contextmanager
def step(name):
    print("CHECK:",name,flush=True)
    try:
        yield
    except DirectError as exc:
        print("BLOCK:",name,str(exc),file=sys.stderr,flush=True)
        raise
    except Exception as exc:
        print("BLOCK:",name,type(exc).__name__,file=sys.stderr,flush=True)
        raise
    print("PASS:",name,flush=True)


def docker(*args):
    return subprocess.check_output([*DOCKER,*args],env=DOCKER_ENV,stderr=subprocess.DEVNULL,timeout=60)

def host_guard(cid):
    if os.geteuid()!=0 or not re.fullmatch("[a-f0-9]{64}",cid):
        raise DirectError("ROOT_OR_CONTAINER_ID")
    info=json.loads(docker("inspect","--type","container",cid))[0]
    image=json.loads(docker("image","inspect",IMAGE))[0]
    digests={p+"@"+IMAGE.split("@")[1] for p in ("postgres","library/postgres","docker.io/library/postgres","index.docker.io/library/postgres")}
    if (info["Id"]!=cid or info["Name"]!="/ashare-postgres" or not info["State"]["Running"] or info["State"].get("Health",{}).get("Status")!="healthy"
        or info["Config"]["Image"] not in (IMAGE,"postgres:18.7-alpine") or info["Image"]!=image["Id"]
        or image["Os"]!="linux" or image["Architecture"]!="amd64" or not digests.intersection(image.get("RepoDigests") or [])
        or info["HostConfig"]["Privileged"] or info["HostConfig"]["PortBindings"]!={"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5432"}]}):
        raise DirectError("CONTAINER_IMAGE_OR_BINDING")
    network_guard(info)
    return info

def network_guard(info):
    networks=info["NetworkSettings"]["Networks"]
    if set(networks) not in ({"ashare-db"},{"ashare-db","1panel-network"}):
        raise DirectError("NETWORK_MEMBERSHIP")
    for name in sorted(networks):
        net=json.loads(docker("network","inspect",name))[0]
        if net["Name"]!=name or net["Id"]!=networks[name]["NetworkID"] or net["Driver"]!="bridge":
            raise DirectError("NETWORK_IDENTITY")
        if name=="ashare-db" and net["Internal"] is not True:
            raise DirectError("NETWORK_NOT_INTERNAL")
        if name=="1panel-network":
            # Only the reviewed management network and its observed sole
            # attachment are allowed. Do not claim whole-container isolation.
            if net["Internal"] is not False or set(net.get("Containers") or {})!={info["Id"]}:
                raise DirectError("MANAGEMENT_NETWORK_SCOPE")

def env_guard():
    if os.getenv("ASHARE_MIGRATION_MODE","isolated")!="isolated":
        raise DirectError("UNEXPECTED_MODE")
    if (os.getenv("PGHOST"),os.getenv("PGPORT"),os.getenv("PGDATABASE"))!=("127.0.0.1","5432",TARGET):
        raise DirectError("TARGET_ENDPOINT")
    if not os.getenv("PGUSER") or any(os.getenv(k) for k in ("PGPASSWORD","PGHOSTADDR","PGSERVICE","PGSERVICEFILE","PGOPTIONS","PGTARGETSESSIONATTRS")):
        raise DirectError("CONNECTION_OPTIONS")
    p=Path(os.environ["PGPASSFILE"])
    s=p.lstat()
    if not p.is_absolute() or not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_mode&0o077:
        raise DirectError("PGPASS_PERMISSIONS")
    # Whitelist libpq environment, do not inherit source connection options.
    return {**{k:os.environ[k] for k in ("PGHOST","PGPORT","PGDATABASE","PGUSER","PGPASSFILE")},
            "PGSSLMODE":"disable","PGCONNECT_TIMEOUT":"10","PATH":os.environ["PATH"],"LC_ALL":"C","HOME":"/root"}

def confirm_backend(c,cid,nonce):
    # Bind the authenticated TCP session to this exact Docker container, even
    # when an existing non-superuser owner cannot call pg_control_system().
    row=c.execute("SELECT current_database(),current_user,session_user,pg_backend_pid(),(SELECT oid::int FROM pg_database WHERE datname=current_database()),(SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())").fetchone()
    db,user,session,pid,oid,owner=row
    if db!=TARGET or user!=owner or session!=owner:
        raise DirectError("DATABASE_OWNER")
    q=f"""SELECT json_build_array((pg_control_system()).system_identifier::text,d.oid::int,(SELECT count(*) FROM pg_stat_activity x WHERE x.datid=d.oid AND x.pid<>a.pid AND x.backend_type='client backend'))
    FROM pg_database d JOIN pg_stat_activity a ON a.datid=d.oid
    WHERE d.datname='ashare' AND a.pid={int(pid)} AND a.application_name='{nonce}' AND a.usename=pg_get_userbyid(d.datdba);"""
    raw=docker("exec","--user","postgres",cid,"psql","-X","-w","-qAt","-h","/var/run/postgresql","-U","postgres","-d","postgres","-v","ON_ERROR_STOP=1","-c",q).decode().strip()
    if not raw: raise DirectError("BACKEND_BINDING")
    system_id,db_oid,other_sessions=json.loads(raw)
    if db_oid!=oid or not re.fullmatch(r"[0-9]{1,20}",system_id):
        raise DirectError("DATABASE_IDENTITY")
    if other_sessions:
        raise DirectError("OTHER_TARGET_SESSIONS")
    return {"container_id":cid,"system_identifier":system_id,"database_oid":oid,"owner":owner}

def snapshot(c):
    readonly(c)
    try: return json_value(inventory(c))
    finally: c.execute("ROLLBACK")

def assert_no_user_relations(c):
    # Reject foreign-owned or unreadable existing tables before inventory()
    # attempts a data scan. The full catalog check below remains mandatory.
    if c.execute("SELECT EXISTS(SELECT 1 FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('r','p','v','m','S','f'))").fetchone()[0]:
        raise DirectError("TARGET_NOT_EMPTY")

def empty_target(doc):
    if doc["schemas"]!=[["public"]] or any(doc[k] for k in ("relations","functions","types","rules","large_objects","policies","triggers")) or any(doc["special_objects"].values()) or any(e[0]!="plpgsql" for e in doc["extensions"]):
        raise DirectError("TARGET_NOT_EMPTY")

def source_evidence(root):
    src=load(root/"source.json")
    dump=root/"source.dump"
    if digest(dump)!=src["dump_sha256"] or dump.stat().st_size!=src["dump_bytes"] or digest(root/"source.toc")!=src["toc_sha256"]:
        raise DirectError("BACKUP_CHECKSUM")
    if src["schemas"]!=[["public"]] or {(r["schema"],r["name"]) for r in src["tables"]}!={("public",t) for t in TABLES} or {(r[0],r[1],r[2]) for r in src["relations"]}!={("public",t,"r") for t in TABLES}:
        raise DirectError("SOURCE_TABLE_SCOPE")
    # The expected application has no custom executable objects or extensions.
    if any(src[k] for k in ("functions","triggers","policies","rules","large_objects","types")) or any(src["special_objects"].values()) or any(e[0]!="plpgsql" for e in src["extensions"]):
        raise DirectError("SOURCE_CUSTOM_OBJECTS")
    return src,dump

def compare(source,target):
    excluded={"database","captured_at","acl_evidence","dump_sha256","dump_bytes","toc_sha256"}
    return sorted(k for k in set(source)-excluded if source[k]!=target.get(k))

def capacity(root,source_bytes=0):
    avail=int(next(l.split()[1] for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemAvailable:")))*1024
    paths=(root,Path("/var/lib/docker"))
    if avail<1536*1024**2 or any(shutil.disk_usage(p).free<max(10*1024**3,source_bytes*4+10*1024**3) for p in paths):
        raise DirectError("CAPACITY")

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("check","restore","verify"))
    p.add_argument("--root",required=True)
    p.add_argument("--container-id",required=True)
    p.add_argument("--approved-target-read",action="store_true")
    p.add_argument("--approved-final-restore",action="store_true")
    a=p.parse_args()
    if not a.approved_target_read or (a.action=="restore" and not a.approved_final_restore):
        raise DirectError("APPROVAL_REQUIRED")
    root=Path(a.root).resolve()
    s=root.stat()
    if not root.is_dir() or s.st_uid!=0 or s.st_mode&0o077:
        raise DirectError("BACKUP_DIRECTORY")
    with step("connection-environment"):
        env=env_guard()
    with step("container-image-network"):
        before=host_guard(a.container_id)
    nonce="ashare_direct_"+secrets.token_hex(16)
    with psycopg.connect(host=env["PGHOST"],port=5432,dbname=TARGET,user=env["PGUSER"],passfile=env["PGPASSFILE"],sslmode="disable",connect_timeout=10,application_name=nonce,autocommit=True) as c:
        with step("database-owner-backend-and-idle"):
            ident=confirm_backend(c,a.container_id,nonce)
        with step("existing-application-roles"):
            role_guard(c)
        if a.action=="check":
            with step("empty-target-catalog"):
                assert_no_user_relations(c)
                empty_target(snapshot(c))
            with step("host-capacity"):
                capacity(root)
            write_json(root/"direct-target.json",ident)
            print("PASS: empty local ashare, existing owner and existing application roles verified")
            return
        (root/"direct-verified.json").unlink(missing_ok=True)
        if ident!=load(root/"direct-target.json"):
            raise DirectError("PREFLIGHT_IDENTITY")
        with step("source-checksum-and-scope"):
            src,dump=source_evidence(root)
        if a.action=="restore":
            if (root/"target-before.dump").exists() or (root/"target-before.dump").is_symlink():
                raise DirectError("PREVIOUS_RESTORE")
            with step("restore-capacity"):
                capacity(root,int(src["database"][2]))
            c.execute("SELECT pg_advisory_lock(617392002)")
            with step("locked-empty-target-catalog"):
                assert_no_user_relations(c)
                target_before=snapshot(c)
                empty_target(target_before)
            # Preserve original schema and database-local ACL evidence.
            with step("original-target-backup"):
                subprocess.run(["pg_dump","-Fc","--create","--file="+str(root/"target-before.dump")],env=env,check=True,timeout=1800,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            write_json(root/"target-before.json",{"identity":ident,"sha256":digest(root/"target-before.dump"),"acl_evidence":target_before["acl_evidence"]})
            with step("pre-restore-backend-binding"):
                confirm_backend(c,a.container_id,nonce)
            with step("single-transaction-pg-restore"):
                subprocess.run(["pg_restore","--exit-on-error","--single-transaction","--no-owner","--no-acl","--dbname=ashare",str(dump)],env=env,check=True,timeout=1800,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        with step("logical-catalog-and-row-hashes"):
            target=snapshot(c)
            mismatches=compare(src,target)
            if mismatches:
                write_json(root/"direct-db-mismatch.json",{"mismatches":mismatches})
                raise DirectError("LOGICAL_MISMATCH")
        with step("existing-application-acl"):
            if a.action=="restore":
                with c.transaction():
                    plan=policy(c,POLICY)
                    grant(c,plan,database=TARGET)
                    checks=verify(c,plan)
            else:
                checks=verify(c,policy(c,POLICY))
        after=host_guard(a.container_id)
        if after["State"]["StartedAt"]!=before["State"]["StartedAt"] or after["RestartCount"]!=before["RestartCount"] or confirm_backend(c,a.container_id,nonce)!=ident:
            raise DirectError("FINAL_IDENTITY")
        write_json(root/"direct-verified.json",{"passed":True,"identity":ident,"source_sha256":src["dump_sha256"],"tables":target["tables"],"acl_checks":checks})
        print("PASS: full logical consistency and existing application ACL checks",checks)

if __name__=="__main__":
    try:
        main()
    except DirectError as exc:
        print("FAILED:",str(exc),file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        print("FAILED:",type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
