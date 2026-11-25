"""Inventory, snapshot+dump, isolated restore and logical verification via libpq.
Credentials are supplied through a private PGPASSFILE, never command arguments.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone
import psycopg
from psycopg import sql
from common import digest, load, main_guard, write_json

LOCAL = {"127.0.0.1", "::1", "localhost"}
TARGET = "ashare_migration"
SYSTEM = "n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'"

def check_env(live=False, target=False):
    host = os.environ.get("PGHOST", "")
    db = os.environ.get("PGDATABASE", "")
    if not host or not db or "," in host or os.getenv("PGHOSTADDR") or os.getenv("PGSERVICE"):
        raise ValueError("explicit single PGHOST/PGDATABASE required")
    if target and (host not in LOCAL or db != TARGET):
        raise ValueError("restore and verify restricted to isolated loopback database")
    if host not in LOCAL:
        if target or not live or "-pooler" in host or os.getenv("PGSSLMODE") != "verify-full":
            raise ValueError("remote source requires approved direct TLS connection")
    if not live and not target and (host not in LOCAL or db != "ashare_source"):
        raise ValueError("simulation source only")
    if os.getenv("PGPASSWORD"):
        raise ValueError("use private PGPASSFILE")
    passfile = os.getenv("PGPASSFILE")
    if passfile and Path(passfile).stat().st_mode & 0o077:
        raise ValueError("PGPASSFILE must not be group/world accessible")

def connect(live=False, target=False):
    check_env(live, target)
    return psycopg.connect("", connect_timeout=10, autocommit=True)

def rows(c, query):
    return [list(r) for r in c.execute(query).fetchall()]

def inventory(c):
    c.execute("SET LOCAL search_path = pg_catalog")
    c.execute("SET LOCAL TIME ZONE 'UTC'")
    c.execute("SET LOCAL DateStyle = 'ISO, YMD'")
    c.execute("SET LOCAL IntervalStyle = 'postgres'")
    c.execute("SET LOCAL extra_float_digits = 3")
    c.execute("SET LOCAL bytea_output = 'hex'")
    data = {
        "database": c.execute("SELECT current_database(), current_setting('server_version_num'), pg_database_size(current_database())").fetchone(),
        "schemas": rows(c, "SELECT nspname FROM pg_namespace n WHERE " + SYSTEM + " ORDER BY 1"),
        "relations": rows(c, "SELECT n.nspname, r.relname, r.relkind::text, r.relpersistence::text, pg_get_partkeydef(r.oid), pg_get_expr(r.relpartbound,r.oid) FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('r','p','v','m','S','f') ORDER BY 1,2"),
        "columns": rows(c, "SELECT n.nspname,r.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity::text,a.attgenerated::text,pg_get_expr(d.adbin,d.adrelid),co.collname FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid JOIN pg_namespace n ON n.oid=r.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=r.oid AND d.adnum=a.attnum LEFT JOIN pg_collation co ON co.oid=a.attcollation WHERE "+SYSTEM+" AND r.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3"),
        "indexes": rows(c, "SELECT n.nspname,r.relname,pg_get_indexdef(r.oid),i.indisvalid,i.indisready FROM pg_index i JOIN pg_class r ON r.oid=i.indexrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" ORDER BY 1,2"),
        "constraints": rows(c, "SELECT n.nspname,coalesce(r.relname,t.typname),co.conname,pg_get_constraintdef(co.oid),co.convalidated FROM pg_constraint co JOIN pg_namespace n ON n.oid=co.connamespace LEFT JOIN pg_class r ON r.oid=co.conrelid LEFT JOIN pg_type t ON t.oid=co.contypid WHERE "+SYSTEM+" AND co.contype <> 'n' ORDER BY 1,2,3"),
        "views": rows(c, "SELECT n.nspname,r.relname,pg_get_viewdef(r.oid,false) FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('v','m') ORDER BY 1,2"),
        "functions": rows(c, "SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE "+SYSTEM+" AND p.prokind IN ('f','p','w') ORDER BY 1,2,3"),
        "extensions": rows(c, "SELECT extname,extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1"),
        "sequences": rows(c, "SELECT n.nspname,r.relname,format_type(s.seqtypid,NULL),s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_sequence s JOIN pg_class r ON r.oid=s.seqrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" ORDER BY 1,2"),
        "triggers": rows(c, "SELECT n.nspname,r.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled::text FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND NOT t.tgisinternal ORDER BY 1,2,3"),
        "policies": rows(c, "SELECT schemaname,tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies ORDER BY 1,2,3"),
        "rls": rows(c, "SELECT n.nspname,r.relname,r.relrowsecurity,r.relforcerowsecurity FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('r','p') ORDER BY 1,2"),
        "acl_evidence": rows(c, "SELECT n.nspname,r.relname,pg_get_userbyid(r.relowner),r.relacl::text FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('r','p','v','m','S','f') ORDER BY 1,2"),
        "sequence_values": [],
        "tables": [],
    }
    data["types"] = rows(c, "SELECT n.nspname,t.typname,t.typtype::text,format_type(t.typbasetype,t.typtypmod),t.typnotnull,t.typdefault FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE "+SYSTEM+" AND t.typtype IN ('d','e','r','m') ORDER BY 1,2")
    data["enums"] = rows(c, "SELECT n.nspname,t.typname,e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE "+SYSTEM+" ORDER BY 1,2,e.enumsortorder")
    data["rules"] = rows(c, "SELECT n.nspname,r.relname,w.rulename,pg_get_ruledef(w.oid) FROM pg_rewrite w JOIN pg_class r ON r.oid=w.ev_class JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND w.rulename <> '_RETURN' ORDER BY 1,2,3")
    data["large_objects"] = rows(c, "SELECT oid FROM pg_largeobject_metadata ORDER BY 1")
    if data["large_objects"]:
        raise ValueError("large objects require separate approved migration strategy")
    data["special_objects"] = {
        "publications": rows(c,"SELECT pubname FROM pg_publication ORDER BY 1"),
        "foreign_servers": rows(c,"SELECT srvname FROM pg_foreign_server ORDER BY 1"),
        "event_triggers": rows(c,"SELECT evtname FROM pg_event_trigger ORDER BY 1"),
        "custom_aggregates": rows(c,"SELECT n.nspname,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE "+SYSTEM+" AND p.prokind='a' ORDER BY 1,2"),
        "custom_operators": rows(c,"SELECT n.nspname,o.oprname FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace WHERE "+SYSTEM+" ORDER BY 1,2"),
    }
    if any(data["special_objects"].values()):
        raise ValueError("special objects require approved catalog/TOC review")
    data["acl_evidence"] = {"relations":data["acl_evidence"],
        "schemas":rows(c,"SELECT nspname,pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace n WHERE "+SYSTEM+" ORDER BY 1"),
        "functions":rows(c,"SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_userbyid(p.proowner),p.proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE "+SYSTEM+" ORDER BY 1,2,3"),
        "defaults":rows(c,"SELECT pg_get_userbyid(d.defaclrole),n.nspname,d.defaclobjtype::text,d.defaclacl::text FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace ORDER BY 1,2,3"),
        "memberships":rows(c,"SELECT pg_get_userbyid(roleid),pg_get_userbyid(member),admin_option,inherit_option,set_option FROM pg_auth_members ORDER BY 1,2"),
        "database":rows(c,"SELECT pg_get_userbyid(datdba),datacl::text FROM pg_database WHERE datname=current_database()")}
    for schema, name, kind, *_ in data["relations"]:
        ident = sql.Identifier(schema,name)
        if kind == "S":
            value = c.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(ident)).fetchone()
            data["sequence_values"].append([schema,name,*value])
        elif kind in ("r","p","m"):
            # ONLY avoids counting inherited or partitioned rows twice.
            # COPY text serialization is deterministic for built-in types; custom
            # types and cross-version formatting must be independently reviewed.
            count = c.execute(sql.SQL("SELECT count(*) FROM ONLY {}").format(ident)).fetchone()[0]
            h = hashlib.sha256()
            stmt = sql.SQL('COPY (SELECT payload FROM (SELECT row_to_json(t)::text AS payload FROM ONLY {} AS t) x ORDER BY payload COLLATE "C") TO STDOUT').format(ident)
            with c.cursor().copy(stmt) as copy:
                for chunk in copy:
                    h.update(chunk)
            data["tables"].append({"schema":schema,"name":name,"rows":count,"sha256":h.hexdigest()})
        elif kind == "f":
            raise ValueError("foreign table requires separate migration review")
    data["captured_at"] = datetime.now(timezone.utc).isoformat()
    return data

def readonly(c):
    c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    c.execute("SET LOCAL row_security = off")
    c.execute("SET LOCAL statement_timeout = '30min'")
    c.execute("SET LOCAL lock_timeout = '5s'")

def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)

def backup(args):
    root=Path(args.root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest=root/"source.dump"
    meta=root/"source.json"
    temp=root/"source.dump.partial"
    if any(p.exists() or p.is_symlink() for p in (dest,meta,temp,root/"source.toc")):
        raise FileExistsError("use fresh evidence directory")
    with connect(args.approved_live_source) as c:
        readonly(c)
        source=inventory(c)
        # The snapshot-exporting session must stay alive while pg_dump reads.
        # Override only this transaction, with bounded child execution below.
        c.execute("SET LOCAL idle_in_transaction_session_timeout = 0")
        snapshot=c.execute("SELECT pg_export_snapshot()").fetchone()[0]
        client=subprocess.check_output(["pg_dump","--version"],text=True).strip()
        # Use the first version number; distro suffixes are irrelevant.
        major=int(re.search(r"(\d+)\.",client).group(1))
        if major < int(source["database"][1])//10000:
            raise ValueError("pg_dump client older than source")
        try:
            run(["pg_dump","--format=custom","--compress=3","--lock-wait-timeout=5s","--snapshot="+snapshot,"--file="+str(temp)])
            toc=subprocess.check_output(["pg_restore","--list",str(temp)],stderr=subprocess.DEVNULL)
            (root/"source.toc").write_bytes(toc)
            source["toc_sha256"]=digest(root/"source.toc")
            source["dump_sha256"]=digest(temp)
            source["dump_bytes"]=temp.stat().st_size
            os.link(temp,dest)
            write_json(meta,source)
        finally:
            temp.unlink(missing_ok=True)
    print("snapshot and archive evidence saved locally")

def restore(args):
    root=Path(args.root)
    meta=load(root/"source.json")
    dump=root/"source.dump"
    if digest(dump)!=meta["dump_sha256"] or dump.stat().st_size!=meta["dump_bytes"]:
        raise ValueError("dump integrity mismatch")
    if digest(root/"source.toc")!=meta["toc_sha256"]:
        raise ValueError("TOC integrity mismatch")
    if not args.approved_isolated_restore:
        raise ValueError("isolated restore approval required")
    with connect(target=True) as c:
        if c.execute("SELECT current_database()").fetchone()[0]!=TARGET:
            raise ValueError("wrong database")
        if c.execute("SELECT count(*) FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('r','p','v','m','S','f')").fetchone()[0]:
            raise ValueError("target is not empty")
        if c.execute("SELECT count(*) FROM pg_namespace n WHERE "+SYSTEM+" AND n.nspname <> 'public'").fetchone()[0]:
            raise ValueError("target contains custom schemas")
    run(["pg_restore","--exit-on-error","--single-transaction","--no-owner","--no-acl","--dbname="+TARGET,str(dump)])
    print("isolated restore complete; verify separately")

def verify(args):
    (Path(args.root)/"verify-db.json").unlink(missing_ok=True)
    source=load(Path(args.root)/"source.json")
    with connect(target=True) as c:
        readonly(c)
        target=inventory(c)
    excluded={"database","captured_at","acl_evidence","dump_sha256","dump_bytes","toc_sha256"}
    fields=sorted(set(source)-excluded)
    mismatches=[field for field in fields if source[field]!=json_value(target.get(field))]
    write_json(Path(args.root)/"verify-db.json",{"passed":not mismatches,"mismatches":mismatches,"source_dump_sha256":source["dump_sha256"],"target":target})
    if mismatches:
        raise ValueError("logical mismatch; inspect local report")
    print("logical database comparison passed; ACL review still required")

def json_value(value):
    import json
    return json.loads(json.dumps(value))

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["inventory","backup","restore","verify"])
    p.add_argument("--root",required=True)
    p.add_argument("--approved-live-source",action="store_true")
    p.add_argument("--approved-isolated-restore",action="store_true")
    a=p.parse_args()
    if a.action=="inventory":
        with connect(a.approved_live_source) as c:
            readonly(c)
            write_json(Path(a.root)/"inventory-db.json",inventory(c))
        print("database inventory saved locally")
    else:
        globals()[a.action](a)

if __name__=="__main__":
    main_guard(main)
