"""Shared-instance target identity. Only root can publish the approved identity."""
import json
import os
from pathlib import Path
import re
import stat

ROOT=Path("/opt/ashare/migration")
IDENTITY=ROOT/"config/shared-target.json"
ROLES=("ashare_mig_owner","ashare_mig_reader","ashare_mig_writer")
TARGET="ashare_migration"
HBA_RULES=(
    ("local",("all",),ROLES,None,"reject"),
    ("host",(TARGET,),ROLES,"all","scram-sha-256"),
    ("host",("all",),ROLES,"all","reject"),
    ("host",("replication",),ROLES,"all","reject"),
)

def shared():
    mode=os.getenv("ASHARE_MIGRATION_MODE","isolated")
    if mode not in ("isolated","shared"):
        raise ValueError("unknown migration mode")
    return mode=="shared"

def hba_fragment():
    roles=",".join(ROLES)
    return f"local all {roles} reject\nhost {TARGET} {roles} all scram-sha-256\nhost all {roles} all reject\nhost replication {roles} all reject\n"

def hba_guard(rows):
    if len(rows)!=len(HBA_RULES):
        raise ValueError("missing first scoped HBA rules")
    for row,expected in zip(rows,HBA_RULES):
        typ,db,users,addr,auth,options=row
        if (typ,tuple(db),tuple(users),addr,auth)!=expected or options:
            raise ValueError("unexpected first scoped HBA rules")

def validate_record(doc):
    if set(doc)!={"container_id","system_identifier","database_oid","owner","role_oids"}:
        raise ValueError("invalid shared target identity fields")
    if not re.fullmatch("[0-9a-f]{64}",doc["container_id"]) or not re.fullmatch("[0-9]{1,20}",doc["system_identifier"]) or type(doc["database_oid"]) is not int or doc["database_oid"]<=0 or doc["owner"]!=ROLES[0]:
        raise ValueError("invalid shared target identity")
    if set(doc["role_oids"])!=set(ROLES) or any(type(x) is not int or x<=0 for x in doc["role_oids"].values()) or len(set(doc["role_oids"].values()))!=3:
        raise ValueError("invalid shared role identities")
    return doc

def read_record():
    for p in (IDENTITY.parent,*IDENTITY.parent.parents):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
            raise ValueError("untrusted shared identity directory")
    fd=os.open(IDENTITY,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022 or s.st_size>8192:
            raise ValueError("untrusted shared identity file")
        return validate_record(json.loads(f.read(8193)))

def check_target_env():
    if os.getenv("PGHOST")!="127.0.0.1" or os.getenv("PGPORT")!="5432" or os.getenv("PGDATABASE")!=TARGET or os.getenv("PGUSER")!=ROLES[0]:
        raise ValueError("shared target requires dedicated owner on fixed loopback endpoint")
    if any(os.getenv(x) for x in ("PGOPTIONS","PGSERVICE","PGSERVICEFILE","PGHOSTADDR","PGPASSWORD")):
        raise ValueError("unsafe shared connection options")
    expected=ROOT/"config/client/owner.pgpass"
    if os.getenv("PGPASSFILE")!=str(expected):
        raise ValueError("shared target requires dedicated private credential file")
    s=expected.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_mode&0o077:
        raise ValueError("unsafe shared credential file")
    return read_record()

def verify_target(c,doc):
    actual=c.execute("""SELECT current_database(),d.oid::int,pg_get_userbyid(d.datdba),
      current_user,session_user,(pg_control_system()).system_identifier::text
      FROM pg_database d WHERE d.datname=current_database()""").fetchone()
    if actual!=(TARGET,doc["database_oid"],ROLES[0],ROLES[0],ROLES[0],doc["system_identifier"]):
        raise ValueError("shared database or cluster identity mismatch")
    roles=c.execute("""SELECT rolname,oid::int,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
      rolreplication,rolbypassrls,rolinherit,rolconnlimit
      FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname""",(list(ROLES),)).fetchall()
    if len(roles)!=3 or any(oid!=doc["role_oids"].get(name) or not login or any(flags) or lim!=(4 if name==ROLES[0] else 8) for name,oid,login,*flags,lim in roles):
        raise ValueError("shared migration role identity or privileges drift")
    if c.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member OR r.oid=m.roleid WHERE r.rolname=ANY(%s))",(list(ROLES),)).fetchone()[0]:
        raise ValueError("unexpected shared role membership")
