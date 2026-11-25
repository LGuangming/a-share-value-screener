"""Explicit policy for isolated database only; roles must be provisioned separately."""
import argparse
from pathlib import Path
from psycopg import sql
from common import load, write_json, main_guard
from postgres import connect, SYSTEM

READER="screener_reader"
WRITER="screener_writer"
ROLES=(READER,WRITER)
TABLE_PRIVS=("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")
SEQ_PRIVS=("USAGE","SELECT","UPDATE")

def relations(c):
    return c.execute("SELECT n.nspname,r.relname,r.relkind::text FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE "+SYSTEM+" AND r.relkind IN ('r','p','v','m','S','f') ORDER BY 1,2").fetchall()

def role_guard(c):
    for role in ROLES:
        r=c.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls,rolcanlogin,rolconnlimit FROM pg_roles WHERE rolname=%s",(role,)).fetchone()
        if not r or any(r[:5]) or not r[5] or not 0<r[6]<=8:
            raise ValueError("unsafe/missing role")
        if c.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s))",(role,)).fetchone()[0]:
            raise ValueError("unexpected role membership")

def policy(c, path):
    data=load(path)
    allowed={}
    for k in ("read_tables","write_tables","write_sequences"):
        entries=data[k]
        allowed[k]={tuple(x) for x in entries}
        if len(allowed[k])!=len(entries) or any(len(x)!=2 for x in allowed[k]):
            raise ValueError("invalid policy")
    actual={(s,n):k for s,n,k in relations(c)}
    if not allowed["read_tables"] or not allowed["write_tables"]<=allowed["read_tables"]:
        raise ValueError("invalid read/write policy")
    for k,entries in allowed.items():
        kinds=("S",) if k=="write_sequences" else ("r","p") if k=="write_tables" else ("r","p","v","m")
        if any(actual.get(x) not in kinds for x in entries):
            raise ValueError("policy refers to missing or unsupported object")
    return allowed

def grant(c,p):
    role_guard(c)
    # These are isolated database-local ACL changes, not cluster role mutations.
    c.execute("REVOKE ALL ON DATABASE ashare_migration FROM PUBLIC")
    for role in ROLES:
        ident=sql.Identifier(role)
        c.execute(sql.SQL("REVOKE ALL ON DATABASE ashare_migration FROM {}").format(ident))
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE ashare_migration TO {}").format(ident))
    schemas=[r[0] for r in c.execute("SELECT nspname FROM pg_namespace n WHERE "+SYSTEM).fetchall()]
    for schema in schemas:
        s=sql.Identifier(schema)
        c.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC, {}, {}").format(s,*map(sql.Identifier,ROLES)))
        c.execute(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM PUBLIC, {}, {}").format(s,*map(sql.Identifier,ROLES)))
        for role in ROLES:
            # Prevent future default PUBLIC EXECUTE for objects created by this owner.
            c.execute(sql.SQL("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"))
            c.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} REVOKE ALL ON TABLES FROM {}").format(s,sql.Identifier(role)))
        for role,keys in ((READER,("read_tables",)),(WRITER,("read_tables","write_tables","write_sequences"))):
            if any(x[0]==schema for key in keys for x in p[key]):
                c.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(s,sql.Identifier(role)))
    for s,n,kind in relations(c):
        obj=sql.Identifier(s,n)
        typ=sql.SQL("SEQUENCE" if kind=="S" else "TABLE")
        c.execute(sql.SQL("REVOKE ALL ON {} {} FROM PUBLIC, {}, {}").format(typ,obj,*map(sql.Identifier,ROLES)))
        if (s,n) in p["read_tables"]:
            c.execute(sql.SQL("GRANT SELECT ON {} TO {}, {}").format(obj,*map(sql.Identifier,ROLES)))
        if (s,n) in p["write_tables"]:
            c.execute(sql.SQL("GRANT INSERT, UPDATE, DELETE ON {} TO {}").format(obj,sql.Identifier(WRITER)))
        if (s,n) in p["write_sequences"]:
            c.execute(sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(obj,sql.Identifier(WRITER)))

def verify(c,p):
    role_guard(c)
    checks=0
    for role in ROLES:
        for priv in ("CONNECT","CREATE","TEMP"):
            actual=c.execute("SELECT has_database_privilege(%s,current_database(),%s)",(role,priv)).fetchone()[0]
            if actual!=(priv=="CONNECT"):
                raise ValueError("database ACL mismatch")
            checks+=1
        for s,n,kind in relations(c):
            obj=sql.Identifier(s,n).as_string(c)
            for priv in SEQ_PRIVS if kind=="S" else TABLE_PRIVS:
                expected=(kind=="S" and role==WRITER and (s,n) in p["write_sequences"] and priv in ("USAGE","SELECT")) or (kind!="S" and (s,n) in p["read_tables"] and priv=="SELECT") or (kind!="S" and role==WRITER and (s,n) in p["write_tables"] and priv in ("INSERT","UPDATE","DELETE"))
                fn="has_sequence_privilege" if kind=="S" else "has_table_privilege"
                actual=c.execute("SELECT "+fn+"(%s,%s,%s)",(role,obj,priv)).fetchone()[0]
                if actual!=expected:
                    raise ValueError("relation ACL mismatch")
                checks+=1
            if kind!="S":
                # Column grants can otherwise bypass table-level checks.
                for priv in ("SELECT","INSERT","UPDATE","REFERENCES"):
                    actual=c.execute("SELECT has_any_column_privilege(%s,%s,%s)",(role,obj,priv)).fetchone()[0]
                    expected=((s,n) in p["read_tables"] and priv=="SELECT") or (role==WRITER and (s,n) in p["write_tables"] and priv in ("INSERT","UPDATE"))
                    if actual!=expected:
                        raise ValueError("column ACL mismatch")
                    checks+=1
        for (s,) in c.execute("SELECT nspname FROM pg_namespace n WHERE "+SYSTEM).fetchall():
            expected=any(x[0]==s for k in (("read_tables",) if role==READER else ("read_tables","write_tables","write_sequences")) for x in p[k])
            actual=c.execute("SELECT has_schema_privilege(%s,%s,'USAGE'),has_schema_privilege(%s,%s,'CREATE')",(role,s,role,s)).fetchone()
            if actual!=(expected,False):
                raise ValueError("schema ACL mismatch")
            checks+=2
        if c.execute("SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE "+SYSTEM+" AND has_function_privilege(%s,p.oid,'EXECUTE'))",(role,)).fetchone()[0]:
            raise ValueError("unexpected function execute privilege")
    return checks

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["apply","verify"])
    p.add_argument("--policy",required=True)
    p.add_argument("--root",required=True)
    p.add_argument("--approved-isolated-acl",action="store_true")
    a=p.parse_args()
    if a.action=="apply" and not a.approved_isolated_acl:
        raise ValueError("isolated ACL approval required")
    (Path(a.root)/"verify-acl.json").unlink(missing_ok=True)
    with connect(target=True) as c:
        with c.transaction():
            plan=policy(c,a.policy)
            if a.action=="apply":
                grant(c,plan)
            count=verify(c,plan)
    write_json(Path(a.root)/"verify-acl.json",{"passed":True,"checks":count})
    print("isolated ACL checks passed:",count)

if __name__=="__main__":
    main_guard(main)
