#!/usr/bin/python3
"""Local root-only shared-instance administration. Never stops a container."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from shared_target import ROOT, IDENTITY, ROLES, TARGET, hba_fragment, hba_guard, validate_record, read_record
from image_lock import IMAGE_REF, validate_image
from ops_dispatch import trusted_dir, capacity

DOCKER=["/usr/bin/docker","--host","unix:///var/run/docker.sock"]
ENV={"PATH":"/usr/bin:/bin","HOME":"/root","LANG":"C","LC_ALL":"C"}
NAME="ashare-postgres"

def docker(*args, data=None):
    return subprocess.check_output([*DOCKER,*args],input=data,env=ENV,stderr=subprocess.DEVNULL,timeout=60)

def instance(cid):
    if not re.fullmatch("[0-9a-f]{64}",cid):
        raise ValueError("full approved container ID required")
    info=json.loads(docker("inspect","--type","container",cid))[0]
    if info["Id"]!=cid or info["Name"]!="/"+NAME or not info["State"]["Running"] or info["State"].get("Health",{}).get("Status")!="healthy":
        raise ValueError("unexpected shared container")
    if info["Config"]["Image"] not in (IMAGE_REF,"postgres:18.7-alpine") or info["Image"]!=validate_image(json.loads(docker("image","inspect",IMAGE_REF))[0]):
        raise ValueError("shared container image mismatch")
    hc=info["HostConfig"]
    if hc["Privileged"] or hc["PortBindings"]!={"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5432"}]} or set(info["NetworkSettings"]["Networks"])!={"ashare-db"}:
        raise ValueError("unexpected shared binding or network")
    net=json.loads(docker("network","inspect","ashare-db"))[0]
    if not net["Internal"] or net["Driver"]!="bridge":
        raise ValueError("expected existing internal bridge")
    if hc["Memory"]!=800*1024**2 or hc["NanoCpus"]!=1500000000:
        raise ValueError("shared resource configuration mismatch")
    return info

def sql(cid,db,query):
    if db not in ("postgres",TARGET):
        raise ValueError("administration restricted to maintenance or migration database")
    return docker("exec","-i","--user","postgres",cid,"psql","-X","-w","-qAt","-h","/var/run/postgresql","-U","postgres","-d",db,"-v","ON_ERROR_STOP=1",data=query.encode()).decode().strip()

def hba(cid):
    rows=json.loads(sql(cid,"postgres","SELECT coalesce(json_agg(json_build_array(type,database,user_name,address,auth_method,options) ORDER BY rule_number),'[]') FROM (SELECT * FROM pg_hba_file_rules ORDER BY rule_number LIMIT 4) r;"))
    hba_guard(rows)
    if sql(cid,"postgres","SELECT NOT EXISTS(SELECT 1 FROM pg_hba_file_rules WHERE error IS NOT NULL) AND pg_conf_load_time() >= (pg_stat_file(current_setting('hba_file'))).modification;")!="t":
        raise ValueError("HBA invalid or reload not confirmed")

def role_filter():
    return ",".join("'"+r+"'" for r in ROLES)

def identity(cid):
    doc=json.loads(sql(cid,"postgres",f"""SELECT json_build_object('container_id','{cid}',
      'system_identifier',(pg_control_system()).system_identifier::text,
      'database_oid',d.oid::int,'owner',pg_get_userbyid(d.datdba),
      'role_oids',(SELECT json_object_agg(rolname,oid::int) FROM pg_roles WHERE rolname IN ({role_filter()})))
      FROM pg_database d WHERE datname='{TARGET}';"""))
    return validate_record(doc)

def role_guard(cid,login):
    expected="rolcanlogin" if login else "NOT rolcanlogin"
    if sql(cid,"postgres",f"""SELECT count(*)=3 AND bool_and({expected} AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit AND rolconnlimit=CASE WHEN rolname='{ROLES[0]}' THEN 4 ELSE 8 END)
      AND NOT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member OR r.oid=m.roleid WHERE r.rolname IN ({role_filter()}))
      FROM pg_roles WHERE rolname IN ({role_filter()});""")!="t":
        raise ValueError("shared role privilege or login drift")

def denial_probes(cid):
    # All roles are NOLOGIN at initialization. Require actual HBA rejection,
    # not missing password or a successful connection to a different database.
    databases=json.loads(sql(cid,"postgres",f"SELECT json_agg(datname ORDER BY datname) FROM pg_database WHERE datallowconn AND datname<>'{TARGET}';"))
    checks=0
    for db in databases:
        for role in ROLES:
            for host in ("127.0.0.1","/var/run/postgresql"):
                p=subprocess.run([*DOCKER,"exec","--user","postgres",cid,"env","-i","PATH=/usr/local/bin:/usr/bin:/bin","LC_ALL=C","PGSSLMODE=disable","psql","-X","-w","-At","-h",host,"-U",role,"-d",db,"-c","SELECT 1"],env=ENV,capture_output=True,text=True,timeout=15)
                if p.returncode==0 or "pg_hba.conf rejects connection" not in p.stderr:
                    raise ValueError("expected cross-database HBA rejection not proven")
                checks+=1
    print("PASS: rejected cross-database authentication attempts:",checks)

def init(cid):
    instance(cid)
    trusted_dir(ROOT/"config")
    hba(cid)
    if IDENTITY.exists() or IDENTITY.is_symlink():
        raise ValueError("shared identity already exists")
    if sql(cid,"postgres",f"SELECT NOT EXISTS(SELECT 1 FROM pg_database WHERE datname='{TARGET}') AND NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ({role_filter()}));")!="t":
        raise ValueError("shared database or role name collision")
    # First test HBA rejection even before these roles exist. No target connection
    # to protected databases is expected or accepted.
    denial_probes(cid)
    statements=["BEGIN;","SET LOCAL log_statement='none';","SET LOCAL log_min_error_statement='panic';","SET LOCAL password_encryption='scram-sha-256';"]
    for key,role,limit in zip(("owner","reader","writer"),ROLES,(4,8,8)):
        p=ROOT/"config/private/db-secrets"/key
        trusted_dir(p.parent)
        s=p.lstat()
        if not p.is_file() or p.is_symlink() or s.st_uid!=0 or s.st_mode&0o077:
            raise ValueError("unsafe shared secret")
        pw=p.read_text().strip()
        if not re.fullmatch("[0-9a-f]{64}",pw):
            raise ValueError("invalid generated secret")
        statements.append(f"CREATE ROLE {role} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {limit} PASSWORD '{pw}';")
    sql(cid,"postgres","\n".join(statements+["COMMIT;"]))
    sql(cid,"postgres",f"CREATE DATABASE {TARGET} OWNER {ROLES[0]} TEMPLATE template0 CONNECTION LIMIT 12 ALLOW_CONNECTIONS false;")
    sql(cid,"postgres",f"BEGIN; REVOKE ALL ON DATABASE {TARGET} FROM PUBLIC; GRANT CONNECT ON DATABASE {TARGET} TO {ROLES[1]}, {ROLES[2]}; ALTER DATABASE {TARGET} ALLOW_CONNECTIONS true; COMMIT;")
    sql(cid,TARGET,f"BEGIN; REVOKE ALL ON SCHEMA public FROM PUBLIC; GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {ROLES[0]}; COMMIT;")
    role_guard(cid,False)
    hba(cid)
    denial_probes(cid)
    doc=identity(cid)
    fd=os.open(IDENTITY,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,"w") as f:
        os.fchmod(f.fileno(),0o644)
        json.dump(doc,f,sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    # The record is durable before any login is enabled.
    sql(cid,"postgres","BEGIN; "+" ".join(f"ALTER ROLE {r} LOGIN;" for r in ROLES)+" COMMIT;")
    role_guard(cid,True)
    print("PASS: dedicated migration database and roles created; protected database ACLs unchanged")

def bound(cid):
    info=instance(cid)
    if identity(cid)!=read_record():
        raise ValueError("shared target identity mismatch")
    hba(cid)
    return info

def monitor(cid):
    info=bound(cid)
    role_guard(cid,True)
    pid=info["State"]["Pid"]
    rel=next(x.split(":",2)[2] for x in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if x.startswith("0::"))
    cg=Path("/sys/fs/cgroup")/rel.lstrip("/")
    if not cg.resolve().is_relative_to(Path("/sys/fs/cgroup")): raise ValueError("invalid cgroup")
    ev=dict(x.split() for x in (cg/"memory.events").read_text().splitlines())
    report={**capacity(ROOT),"scope":"whole shared instance","memory_current_bytes":int((cg/"memory.current").read_text()),"memory_peak_bytes":int((cg/"memory.peak").read_text()),"oom_events":int(ev["oom"]),"oom_kills":int(ev["oom_kill"]),"oom_group_kills":int(ev["oom_group_kill"]),"container_restarts":info["RestartCount"],"target_connections":int(sql(cid,"postgres",f"SELECT count(*) FROM pg_stat_activity WHERE datname='{TARGET}';"))}
    report["safe_to_continue"]=not info["State"]["OOMKilled"] and report["container_restarts"]==0 and report["oom_events"]==report["oom_kills"]==report["oom_group_kills"]==0 and report["memory_available_bytes"]>=512*1024**2 and report["disk_free_bytes"]>=10*1024**3
    after=instance(cid)
    if after["State"]["Pid"]!=pid or after["RestartCount"]!=info["RestartCount"]:
        raise ValueError("shared container changed during monitoring")
    print(json.dumps(report,sort_keys=True))
    if not report["safe_to_continue"]: raise ValueError("resource gate failed")

def pause(cid):
    bound(cid)
    role_guard(cid,True)
    # NOLOGIN is limited to the three verified role OIDs. No termination, DB
    # removal, container stop or changes to other roles/databases.
    sql(cid,"postgres","BEGIN; "+" ".join(f"ALTER ROLE {r} NOLOGIN;" for r in ROLES)+" COMMIT;")
    role_guard(cid,False)
    sessions=int(sql(cid,"postgres",f"SELECT count(*) FROM pg_stat_activity WHERE usename IN ({role_filter()});"))
    if sessions:
        raise ValueError("new logins paused; existing sessions must drain")
    print("PASS: shared migration logins paused; database and shared container retained")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["hba-plan","init","monitor","pause"])
    p.add_argument("--container-id")
    p.add_argument("--approved-shared-init",action="store_true")
    p.add_argument("--approved-shared-pause",action="store_true")
    a=p.parse_args()
    if a.action=="hba-plan":
        print(hba_fragment(),end="")
        return
    if os.geteuid()!=0 or not a.container_id: raise PermissionError("local root and approved container ID required")
    if a.action=="init":
        if not a.approved_shared_init: raise PermissionError("shared init approval required")
        init(a.container_id)
    elif a.action=="monitor": monitor(a.container_id)
    else:
        if not a.approved_shared_pause: raise PermissionError("shared pause approval required")
        pause(a.container_id)

if __name__=="__main__":
    try: main()
    except Exception as e:
        print("FAILED:",type(e).__name__,file=sys.stderr)
        raise SystemExit(1) from None
