"""Fail closed before running any VPS test worker command."""
import os
from urllib.parse import urlsplit
import psycopg

def validate(env):
    u=urlsplit(env.get("DATABASE_URL",""))
    if (u.scheme not in ("postgresql","postgres") or u.hostname!="postgres"
        or u.port!=5432 or u.path!="/ashare_test" or u.username!="screener_writer"
        or not u.password or u.query or u.fragment):
        raise ValueError("test writer URL rejected")
    if any(v for k,v in env.items() if k.startswith(("NEON_","TUSHARE_","AWS_"))
           or k in ("DATABASE_READ_URL","SITE_ACCESS_PASSWORD","SITE_SESSION_SECRET")):
        raise ValueError("unexpected credentials")
    if (env.get("ARCHIVE_STORAGE_DRIVER")!="local" or env.get("ARCHIVE_LOCAL_ROOT")!="/data/archive"
        or env.get("ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY")!="false"):
        raise ValueError("test archive configuration rejected")

def main():
    try:
        validate(os.environ)
        with psycopg.connect(os.environ["DATABASE_URL"],connect_timeout=5,
                             options="-c statement_timeout=5000") as conn:
            db,role,can_write=conn.execute(
                "SELECT current_database(), current_user, has_table_privilege(current_user,'public.stocks','INSERT')"
            ).fetchone()
            if (db,role,can_write)!=("ashare_test","screener_writer",True):
                raise ValueError("test writer identity or privilege check failed")
        print("PASS: isolated test writer identity")
    except Exception:
        raise SystemExit("Test Worker guard rejected configuration or database") from None

if __name__=="__main__":
    main()
