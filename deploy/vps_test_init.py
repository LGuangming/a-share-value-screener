#!/usr/bin/env python3
"""11E-17B: local root operator only; never start application containers."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

ROOT = Path("/opt/ashare/test")
PROD = Path("/opt/ashare/archive")
NET = "ashare-test-db"
DB = "ashare-test-postgres"
LABEL = "io.ashare.test-init"
SOURCE = "1a87f659f6e83d9d92d8f04cba5f3891bc769507"
RELEASE = "45746569919"
WEB = "ghcr.io/lguangming/a-share-value-screener-web-test-pr28@sha256:04d24373966809820ad6a5547eb3b16882f6969aa1c8ce5a08204256ca1fdab2"
WORKER = "ghcr.io/lguangming/a-share-value-screener-worker-test-pr28@sha256:69fda6137261884a266993a9d0323175b602cf61ca1ff838d96dd74303c7877a"
PIN = re.compile(r"(?:docker\.io/library/)?postgres@sha256:[a-f0-9]{64}")
HEX = re.compile(r"[a-f0-9]{64}")
BLOBS = {"compose.yaml":"33dd3f152919d0fc9ba1b83e9f9640d22daa10b8",
         "compose.loopback.yaml":"87810cf98796365455feef1375e1805f13af63b4",
         "compose.vps-test.yaml":"63593a355b6c73d53f364228bf6fa43eb6223a84",
         "database/schema.sql":"88f1e1f6965794692daf2f9b328fa8c63dddabcf",
         "tests/fixtures/m12-acl.sql":"594db61dc37bf91a39bcabfc351c18a046ce9d18"}
TABLES = ("stocks","daily_prices","financial_reports","financial_fetch_log","job_runs","price_history","price_history_fetches","screen_results")
DELETES = ("daily_prices","price_history","price_history_fetches","screen_results")
ROLES = ("screener_reader","screener_writer")
PRIVS = ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")
DIRS = {"app":(0,0,0o750), "config":(0,0,0o700), "archive":(10002,10010,0o2750),
        "cache":(0,0,0o750), "cache/nextjs":(10001,10010,0o750),
        "tmp":(10002,10010,0o750), "tmp/duckdb":(10002,10010,0o750),
        "evidence":(0,0,0o700)}
STATE = "config/.init-state.json"
LOCK = "config/.init.lock"
RECEIPT = "evidence/init-receipt.json"


class Blocked(Exception):
    pass


def need(ok, label):
    if not ok:
        raise Blocked(label)


def run(args, data=None, timeout=90):
    env = {k:v for k,v in os.environ.items() if not k.startswith(("DOCKER_","COMPOSE_","PG"))}
    try:
        p = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("local command failed or timed out") from None
    # Never return or print stderr, SQL, env, command arguments or secrets.
    need(p.returncode == 0, "local command failed (exit %d)" % p.returncode)
    return p.stdout


def docker(*args, data=None, timeout=90):
    return run(["docker","--host","unix:///var/run/docker.sock",*args],data,timeout)


def read_json(kind, name):
    data = json.loads(docker(kind,"inspect",name))
    need(isinstance(data,list) and len(data)==1,"unexpected Docker inspect result")
    return data[0]


def names(kind):
    args = ("container","ls","-a") if kind=="container" else ("network","ls")
    return set(docker(*args,"--format","{{.Names}}" if kind=="container" else "{{.Name}}").splitlines())


def safe_dir(p, expected=None):
    need(p.is_absolute() and p.resolve()==p,"noncanonical directory")
    s = p.lstat()
    need(stat.S_ISDIR(s.st_mode) and not stat.S_ISLNK(s.st_mode),"not a real directory")
    if expected is not None:
        need((s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))==expected,"directory owner/mode mismatch: "+p.name)
    return s


def regular(p, mode=0o600):
    need(p.resolve()==p,"noncanonical file")
    s=p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_gid==0 and
         stat.S_IMODE(s.st_mode)==mode and s.st_nlink==1,"unsafe file: "+p.name)
    return p.read_bytes()


def exists(p):
    return os.path.lexists(p)


def fsync_dir(p):
    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def create_once(p, data, mode=0o600):
    if exists(p):
        need(regular(p,mode)==data,"existing file differs: "+p.name)
        return
    temp=p.parent/(".init-"+secrets.token_hex(16)+".tmp")
    try:
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
        with os.fdopen(fd,"wb") as f:
            os.fchmod(f.fileno(),mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Atomic create without overwriting any existing path.
        os.link(temp,p,follow_symlinks=False)
        temp.unlink()
        fsync_dir(p.parent)
    finally:
        if exists(temp): temp.unlink()


def receipt(data):
    p=ROOT/RECEIPT
    if exists(p):
        regular(p)
        old=json.loads(p.read_text())
        need(old.get("owner_id")==data["owner_id"],"unowned prior receipt")
    temp=p.parent/(".receipt-"+secrets.token_hex(16)+".tmp")
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        json.dump(data,f,indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp,p)
    fsync_dir(p.parent)


def sources(path):
    # Lock exact published application's file content, not arbitrary HEAD.
    need(path.is_absolute() and path.resolve()==path,"source path not canonical")
    result={}
    for name,sha in BLOBS.items():
        p=path/name
        need(p.resolve()==p and p.is_file(),"source file missing or symlink")
        data=p.read_bytes()
        blob=hashlib.sha1(("blob %d\0"%len(data)).encode()+data).hexdigest()
        need(blob==sha,"reviewed source blob mismatch: "+name)
        result[name]=data
    return result


def state_new(pin):
    return {"version":1,"owner_id":secrets.token_hex(32),"source":SOURCE,"release":RELEASE,
            "postgres_pin":pin, **{k:secrets.token_hex(32) for k in ("admin","reader","writer","site","session")}}


def state_check(s,pin):
    keys={"version","owner_id","source","release","postgres_pin","admin","reader","writer","site","session"}
    need(isinstance(s,dict) and set(s)==keys and s["version"]==1 and s["source"]==SOURCE and
         s["release"]==RELEASE and s["postgres_pin"]==pin,"initialization state mismatch")
    vals=[s[k] for k in ("owner_id","admin","reader","writer","site","session")]
    need(all(isinstance(x,str) and HEX.fullmatch(x) for x in vals) and len(set(vals))==len(vals),"invalid private initialization state")


def config(s):
    return {
        "config/.env": ("WEB_IMAGE="+WEB+"\nWORKER_IMAGE="+WORKER+"\nDATABASE_NETWORK="+NET+
                        "\nASHARE_LOOPBACK_PORT=3198\nARCHIVE_DIR=/opt/ashare/test/archive\n"
                        "WEB_CACHE_DIR=/opt/ashare/test/cache/nextjs\nWORKER_TMP_DIR=/opt/ashare/test/tmp\n").encode(),
        "config/web.env": ("DATABASE_READ_URL=postgresql://screener_reader:"+s["reader"]+
                           "@postgres:5432/ashare_test\nSITE_ACCESS_PASSWORD="+s["site"]+
                           "\nSITE_SESSION_SECRET="+s["session"]+"\n").encode(),
        "config/worker.env": ("DATABASE_URL=postgresql://screener_writer:"+s["writer"]+
                              "@postgres:5432/ashare_test\nARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY=false\n").encode(),
        "config/postgres.env": ("POSTGRES_USER=fixture_admin\nPOSTGRES_DB=ashare_test\nPOSTGRES_PASSWORD="+s["admin"]+"\n").encode(),
    }


def check_mounts():
    # Reject separately mounted/bind-mounted test descendants and root alias.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        mount=line.split()[4].replace(r"\040"," ").replace(r"\011","\t")
        need(mount!=str(ROOT) and not mount.startswith(str(ROOT)+"/"),"mount inside test root")
    need(ROOT.resolve()==ROOT and PROD.resolve()==PROD,"test or production path alias")
    need(not os.path.samefile(ROOT,PROD),"test root aliases production archive")


def paths_check(s, files):
    for p in (Path("/opt"),ROOT.parent):
        st=safe_dir(p)
        need(st.st_uid==0 and not st.st_mode&0o022,"untrusted parent directory")
    safe_dir(ROOT,(0,0,0o750))
    safe_dir(PROD)
    check_mounts()
    allowed={"app","config","archive","cache","tmp","evidence","postgres"}
    need({p.name for p in ROOT.iterdir()}<=allowed,"unexpected test root content")
    for rel, spec in DIRS.items():
        p=ROOT/rel
        if exists(p): safe_dir(p,spec)
    for rel, expected in (("archive",set()),("cache",{"nextjs"}),("cache/nextjs",set()),("tmp",{"duckdb"}),("tmp/duckdb",set())):
        p=ROOT/rel
        if exists(p):
            need({x.name for x in p.iterdir()}<=expected,"existing test data: "+rel)
    allowed_files={"app/"+k:v for k,v in files.items() if k.startswith("compose.")}
    if s is not None: allowed_files.update(config(s))
    for rel in ("app","config","evidence"):
        p=ROOT/rel
        if not exists(p): continue
        for item in p.iterdir():
            key=rel+"/"+item.name
            if key==LOCK:
                regular(item)
            elif s is not None and key in (STATE,RECEIPT):
                regular(item)
                if key==RECEIPT:
                    need(json.loads(item.read_text()).get("owner_id")==s["owner_id"],"receipt owner mismatch")
            elif s is not None and key in allowed_files:
                need(regular(item,0o644 if rel=="app" else 0o600)==allowed_files[key],"existing file differs: "+key)
            else:
                raise Blocked("unowned existing file: "+key)
    p=ROOT/"postgres"
    if exists(p):
        st=safe_dir(p)
        need(stat.S_IMODE(st.st_mode)==0o700,"PostgreSQL parent mode mismatch")
        if s is None: need(not any(p.iterdir()) and st.st_uid==0 and st.st_gid==0,"existing PostgreSQL directory not owned")


def check_host():
    need(os.geteuid()==0 and platform.machine()=="x86_64","root on AMD64 required")
    need(Path("/var/run/docker.sock").is_socket(),"local Docker socket unavailable")
    info=json.loads(docker("info","--format","{{json .}}"))
    need(info.get("OSType")=="linux" and info.get("Architecture") in ("x86_64","amd64"),"unexpected Docker engine architecture")
    need(not any("rootless" in x or "userns" in x for x in info.get("SecurityOptions",[])),"unsupported Docker rootless/userns")
    need(str(info.get("CgroupVersion"))=="2","cgroup v2 required")
    version=docker("compose","version","--short").strip()
    v=re.match(r"^v?(\d+)\.(\d+)\.(\d+)",version)
    need(v and tuple(map(int,v.groups()))>=(2,24,4),"Compose >=2.24.4 required")
    need(os.cpu_count() is not None and os.cpu_count()>=4,"at least four host CPUs required for planned test")
    mem=int(re.search(r"^MemAvailable:\s+(\d+) kB",Path("/proc/meminfo").read_text(),re.M)[1])//1024
    # Before first launch: 512 MiB PG + 1792 MiB future apps + 512 MiB reserve.
    need(mem>=2816,"insufficient available host memory (2816 MiB required)")
    for p in (ROOT,Path(info["DockerRootDir"])):
        u=shutil.disk_usage(p)
        st=os.statvfs(p)
        need(u.free>=max(5*1024**3,u.total//5) and st.f_favail>=10000,"insufficient storage or inodes")
    ports=run(["ss","-H","-lnt"])
    need(not any(re.search(r":3198\s",line) for line in ports.splitlines()),"test loopback port occupied")
    # No production connection; metadata only.
    prod=read_json("network","ashare-db")
    need(prod.get("Internal") is True and {x["Name"] for x in (prod.get("Containers") or {}).values()}=={"ashare-postgres"},"production network baseline changed")
    cs=names("container")
    need(not any(n.startswith(("ashare-test-","ashare-test_")) and n!=DB for n in cs),"existing test application container")
    need(not docker("ps","-aq","--filter","label=com.docker.compose.project=ashare-test").strip(),"test Compose project already exists")
    return cs,names("network")


def verify_image(pin):
    d=read_json("image",pin)
    canonical=pin.removeprefix("docker.io/library/")
    need(canonical in {x.removeprefix("docker.io/library/") for x in d.get("RepoDigests",[])},"PostgreSQL image digest not locally verified")
    need(d.get("Os")=="linux" and d.get("Architecture")=="amd64","PostgreSQL image platform mismatch")
    need("PG_VERSION=18.7" in d.get("Config",{}).get("Env",[]),"PostgreSQL 18.7 image required")
    need(re.fullmatch(r"sha256:[0-9a-f]{64}",d.get("Id","")),"invalid image ID")
    return d["Id"]


def verify_net(s):
    n=read_json("network",NET)
    need(n.get("Name")==NET and n.get("Internal") is True and n.get("Driver")=="bridge" and n.get("Scope")=="local"
         and (n.get("Labels") or {}).get(LABEL)==s["owner_id"],"unowned or noninternal test network")
    need({x["Name"] for x in (n.get("Containers") or {}).values()}<={DB},"unexpected test network member")
    return n["Id"]


def verify_container(s,image_id,network_id):
    d=read_json("container",DB)
    cfg=d["Config"]; host=d["HostConfig"]
    need(d.get("Name")=="/"+DB and (cfg.get("Labels") or {}).get(LABEL)==s["owner_id"] and d.get("Image")==image_id,"unowned or changed test database container")
    net=d["NetworkSettings"]["Networks"]
    need(set(net)=={NET} and "postgres" in (net[NET].get("Aliases") or []),"test DB network/alias mismatch")
    # Docker may defer endpoint ID assignment until start; require the explicit
    # network mode and exact endpoint on running containers.
    running=d.get("State",{}).get("Running",False)
    need(host.get("NetworkMode") in (NET,network_id) and (net[NET]["NetworkID"]==network_id or
         (not running and net[NET]["NetworkID"] in ("",None))),"test DB endpoint mismatch")
    need(not host.get("Privileged") and not host.get("PublishAllPorts") and not host.get("PortBindings")
         and host.get("Memory")==512*1024**2 and host.get("NanoCpus")==1000000000
         and host.get("RestartPolicy",{}).get("Name") in ("no","")
         and "no-new-privileges:true" in (host.get("SecurityOpt") or []),"test DB resource or security configuration mismatch")
    mounts=d["Mounts"]
    need(len(mounts)==1 and mounts[0]["Type"]=="bind" and mounts[0]["Source"]==str(ROOT/"postgres")
         and mounts[0]["Destination"]=="/var/lib/postgresql" and mounts[0]["RW"],"test DB mount mismatch")
    env=dict(x.split("=",1) for x in cfg["Env"] if "=" in x)
    need(all(env.get(k)==v for k,v in {"POSTGRES_USER":"fixture_admin","POSTGRES_DB":"ashare_test","POSTGRES_PASSWORD":s["admin"]}.items()),"test DB environment mismatch")
    need(cfg.get("Entrypoint")==["docker-entrypoint.sh"] and cfg.get("Cmd")==["postgres"],"test DB command changed")
    return d


def psql(sql,role="fixture_admin",password=None):
    args=["psql","-X","-qAt","-v","ON_ERROR_STOP=1","-U",role,"-d","ashare_test"]
    if password is None:
        return docker("exec","-i",DB,*args,data=sql,timeout=60).strip()
    # Password goes through stdin, not argv, stdout, or Docker exec env metadata.
    return docker("exec","-i",DB,"sh","-c",
        'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$1" -d ashare_test',
        "ashare-auth",role,data=password+"\n"+sql,timeout=60).strip()


def wait_db(s):
    for _ in range(45):
        try:
            out=psql("SELECT current_database()||'|'||current_user||'|'||current_setting('server_version_num');",password=s["admin"])
            need(out=="ashare_test|fixture_admin|180007","database identity/version mismatch")
            return
        except Blocked as e:
            if str(e)=="database identity/version mismatch": raise
            time.sleep(2)
    raise Blocked("test PostgreSQL did not become ready")


def init_sql(files,s):
    # Use reviewed schema and grants, replacing only the two CI fixture passwords.
    acl=files["tests/fixtures/m12-acl.sql"].decode()
    need(acl.count("PASSWORD 'fixture_only'")==2,"unexpected ACL template")
    acl=acl.replace("screener_reader LOGIN PASSWORD 'fixture_only'","screener_reader LOGIN PASSWORD '"+s["reader"]+"'")
    acl=acl.replace("screener_writer LOGIN PASSWORD 'fixture_only'","screener_writer LOGIN PASSWORD '"+s["writer"]+"'")
    need("fixture_only" not in acl,"fixture password not replaced")
    return ("BEGIN;\nDO $$ BEGIN IF current_database()<>'ashare_test' OR current_user<>'fixture_admin' "
            "THEN RAISE EXCEPTION 'wrong target'; END IF; END $$;\n"+
            files["database/schema.sql"].decode()+"\n"+acl+
            "\nREVOKE CONNECT,TEMP ON DATABASE postgres,template1 FROM PUBLIC;\nCOMMIT;\n")


def acl_sql():
    table_values=",".join("('%s')"%t for t in TABLES)
    role_values=",".join("('%s')"%r for r in ROLES)
    priv_values=",".join("('%s')"%p for p in PRIVS)
    del_values=",".join("'%s'"%t for t in DELETES)
    return """SELECT count(*) FROM (VALUES %s) t(name)
    CROSS JOIN (VALUES %s) r(name) CROSS JOIN (VALUES %s) p(name)
    WHERE has_table_privilege(r.name,'public.'||t.name,p.name) IS NOT DISTINCT FROM
    (p.name='SELECT' OR (r.name='screener_writer' AND
    (p.name IN ('INSERT','UPDATE') OR (p.name='DELETE' AND t.name IN (%s)))));"""%(table_values,role_values,priv_values,del_values)


def verify_acl(s):
    need(psql("SELECT current_database()||'|'||current_user;")=="ashare_test|fixture_admin","test admin identity changed")
    need(psql("SELECT string_agg(tablename,',' ORDER BY tablename) FROM pg_tables WHERE schemaname='public';")==",".join(sorted(TABLES)),"test schema table set mismatch")
    need(psql("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner='fixture_admin';")=="8","test table ownership mismatch")
    need(psql(acl_sql())=="112","M12 table ACL mismatch")
    need(psql("""SELECT count(*) FROM pg_roles r WHERE rolname IN ('screener_reader','screener_writer')
      AND rolcanlogin AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
      AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
      AND has_schema_privilege(rolname,'public','USAGE') AND NOT has_schema_privilege(rolname,'public','CREATE')
      AND has_database_privilege(rolname,'ashare_test','CONNECT') AND NOT has_database_privilege(rolname,'ashare_test','TEMP')
      AND NOT has_database_privilege(rolname,'ashare_test','CREATE')
      AND NOT has_database_privilege(rolname,'postgres','CONNECT')
      AND NOT has_database_privilege(rolname,'template1','CONNECT');""")=="2","role flags, membership, schema or database ACL mismatch")
    for role,key in zip(ROLES,("reader","writer")):
        need(psql("SELECT current_database()||'|'||current_user; SELECT count(*) FROM public.stocks;",role,s[key])==
             "ashare_test|"+role+"\n0","restricted role authentication or nonempty fixture")
    need(psql("SELECT "+ "+".join("(SELECT count(*) FROM public."+t+")" for t in TABLES)+";")=="0","nonempty fixture database; initialization cannot adopt data")


def initialize_db(s,files):
    n=psql("SELECT count(*) FROM pg_tables WHERE schemaname='public';")
    r=psql("SELECT count(*) FROM pg_roles WHERE rolname IN ('screener_reader','screener_writer');")
    if (n,r)==("0","0"):
        psql(init_sql(files,s))
    else:
        need((n,r)==("8","2"),"partial or foreign schema; manual review required")
    verify_acl(s)


def preflight(pin,source):
    need(PIN.fullmatch(pin) is not None,"explicit official PostgreSQL immutable digest required")
    files=sources(source)
    safe_dir(ROOT,(0,0,0o750))
    # Check config parent before reading state.
    if exists(ROOT/"config"): safe_dir(ROOT/"config",DIRS["config"])
    s=json.loads(regular(ROOT/STATE)) if exists(ROOT/STATE) else None
    if s is not None: state_check(s,pin)
    paths_check(s,files)
    cs,ns=check_host()
    image=verify_image(pin)
    if s is None:
        need(DB not in cs and NET not in ns,"unowned same-name Docker resources")
    else:
        need(DB not in cs or NET in ns,"test DB network missing")
        if NET in ns: network_id=verify_net(s)
        if DB in cs: verify_container(s,image,network_id)
        if DB not in cs and exists(ROOT/"postgres"):
            need(not any((ROOT/"postgres").iterdir()),"database container missing but data exists; no automatic adoption")
    return s,files,cs,ns,image


def make_dirs():
    for rel,(uid,gid,mode) in DIRS.items():
        p=ROOT/rel
        if not exists(p):
            p.mkdir(mode=0o700)
            os.chown(p,uid,gid)
            os.chmod(p,mode)
        safe_dir(p,(uid,gid,mode))
    p=ROOT/"postgres"
    if not exists(p): p.mkdir(mode=0o700)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=("plan","init"),default="plan")
    parser.add_argument("--source-dir",type=Path,required=True)
    parser.add_argument("--postgres-pin",required=True)
    parser.add_argument("--confirm",default="")
    args=parser.parse_args()
    os.umask(0o077)
    report=None
    stage="preflight"
    try:
        need(args.mode=="plan" or args.confirm=="INIT-ASHARE-TEST-11E17B","explicit isolated initialization approval required")
        s,files,cs,ns,image=preflight(args.postgres_pin,args.source_dir)
        if args.mode=="plan":
            print(json.dumps({"mode":"plan","passed":True,"source":SOURCE,"postgres_pin":args.postgres_pin,
                              "existing_owned_state":s is not None,"application_started":False,"production_accessed":False}))
            return
        # Config directory is root-private and existing from preflight; create only if absent.
        if not exists(ROOT/"config"): (ROOT/"config").mkdir(mode=0o700)
        safe_dir(ROOT/"config",DIRS["config"])
        fd=os.open(ROOT/LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"r+") as lock:
            regular(ROOT/LOCK)
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            s,files,cs,ns,image=preflight(args.postgres_pin,args.source_dir)
            stage="private_state"
            # State precedes all network, DB and config writes; restart never regenerates passwords.
            if s is None:
                s=state_new(args.postgres_pin)
                create_once(ROOT/STATE,(json.dumps(s,sort_keys=True)+"\n").encode())
            stage="directories"
            make_dirs()
            report={"version":1,"owner_id":s["owner_id"],"source":SOURCE,"release_run":RELEASE,
                    "postgres_pin":args.postgres_pin,"postgres_image_id":image,"complete":False,
                    "application_started":False,"production_accessed":False,"stage":stage}
            def mark(name):
                nonlocal stage
                stage=name
                report.update(stage=name,updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                receipt(report)
                print("STAGE: "+name,flush=True)
            mark("configuration")
            for name,data in config(s).items(): create_once(ROOT/name,data)
            for name,data in files.items():
                if name.startswith("compose."): create_once(ROOT/"app"/name,data,0o644)
            mark("network")
            if NET not in ns:
                docker("network","create","--driver","bridge","--internal","--label",LABEL+"="+s["owner_id"],NET)
            network=verify_net(s)
            mark("database_container")
            if DB not in cs:
                docker("create","--pull","never","--name",DB,"--label",LABEL+"="+s["owner_id"],
                       "--network",NET,"--network-alias","postgres","--restart","no","--memory","512m","--cpus","1",
                       "--security-opt","no-new-privileges:true","--log-opt","max-size=10m","--log-opt","max-file=3",
                       "--mount","type=bind,src="+str(ROOT/"postgres")+",dst=/var/lib/postgresql",
                       "--env-file",str(ROOT/"config/postgres.env"),args.postgres_pin)
            d=verify_container(s,image,network)
            mark("database_start")
            if not d["State"]["Running"]: docker("start",DB)
            verify_net(s)
            verify_container(s,image,network)
            mark("database_ready")
            wait_db(s)
            mark("schema_acl")
            initialize_db(s,files)
            mark("final_checks")
            verify_net(s)
            verify_container(s,image,network)
            report.update(complete=True,acl_assertions=112,roles_verified=True,fixture_rows=0,
                          database="ashare_test",network=NET,web_pin=WEB,worker_pin=WORKER)
            mark("complete")
            print("PASS: isolated database/config initialized; no application started")
    except Exception as exc:
        message=str(exc) if isinstance(exc,Blocked) else "unexpected local validation or initialization failure"
        if report is not None:
            report.update(complete=False,stage=stage,error=message)
            try: receipt(report)
            except Exception: pass
        print("BLOCK: "+stage+": "+message,file=sys.stderr)
        raise SystemExit(1) from None


if __name__=="__main__":
    main()
