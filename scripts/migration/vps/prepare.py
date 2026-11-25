"""Local administrator preparation; never starts containers or modifies existing paths."""
import argparse
import os
from pathlib import Path
import pwd
import secrets
import stat
import shutil
import sys

ROOT=Path("/opt/ashare/migration")
SOURCE=Path(__file__).resolve().parent
PRIVATE=("postgres","archive","inventory","verification","logs","tmp")
GIB=1024**3

class PreparationError(ValueError):
    """Fixed diagnostic codes, no paths or credentials in errors."""

def require_parent(path):
    for p in (path.parent,*path.parent.parents):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode & 0o022:
            raise PreparationError("UNSAFE_PARENT")

def write_new(path, data, mode, uid=0, gid=0):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,"wb") as f:
        os.fchown(f.fileno(),uid,gid)
        os.fchmod(f.fileno(),mode)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

def mkdir(path, mode, uid=0, gid=0):
    path.mkdir(mode=mode)
    os.chown(path,uid,gid)
    path.chmod(mode)

def prepare(root,uid,gid,mode="isolated"):
    if mode not in ("isolated","shared"):
        raise PreparationError("UNKNOWN_MODE")
    require_parent(root)
    if uid<=0 or gid<=0 or root.exists() or root.is_symlink():
        raise PreparationError("EXISTING_ROOT_OR_INVALID_IDENTITY")
    if shutil.disk_usage(root.parent).free < 30*GIB:
        raise PreparationError("INSUFFICIENT_DISK_RESERVE")
    os.umask(0o077)
    mkdir(root,0o711)
    mkdir(root/"bin",0o755)
    mkdir(root/"bin/db-init",0o755)
    mkdir(root/"config",0o711)
    mkdir(root/"config/private",0o700)
    mkdir(root/"config/private/db-secrets",0o755)
    mkdir(root/"config/client",0o700,uid,gid)
    mkdir(root/"ops-public",0o755)
    for d in PRIVATE:
        mkdir(root/d,0o700,uid,gid)
    files = (
        ("compose.yml","compose.yml",0o644),
        ("db-init/10-roles.sh","bin/db-init/10-roles.sh",0o755),
        ("prepare.py","bin/prepare.py",0o755),
        ("ops_dispatch.py","bin/ops_dispatch.py",0o755),
        ("control.py","bin/control.py",0o755),
        ("image_lock.py","bin/image_lock.py",0o644),
    ) if mode=="isolated" else (
        ("shared_admin.py","bin/shared_admin.py",0o755),
        ("../shared_target.py","bin/shared_target.py",0o644),
        ("ops_dispatch.py","bin/ops_dispatch.py",0o755),
        ("image_lock.py","bin/image_lock.py",0o644),
    )
    for filename,dest,file_mode in files:
        write_new(root/dest,(SOURCE/filename).read_bytes(),file_mode)
    # Private parent prevents host access to 0444 files; PostgreSQL's unprivileged
    # entrypoint must be able to read the read-only bind mount inside its container.
    roles=(("admin","postgres"),("owner","migration_owner"),("reader","screener_reader"),("writer","screener_writer")) if mode=="isolated" else (("owner","ashare_mig_owner"),("reader","ashare_mig_reader"),("writer","ashare_mig_writer"))
    port=55432 if mode=="isolated" else 5432
    for key,role in roles:
        value=secrets.token_hex(32)
        write_new(root/"config/private/db-secrets"/key,(value+"\n").encode(),0o444 if mode=="isolated" else 0o600)
        if key!="admin":
            entry=f"127.0.0.1:{port}:ashare_migration:{role}:{value}\n"
            # Separate credential files, not one shared credential for all roles.
            # Only owner file is readable by the local migration identity.
            owner=(uid,gid) if key=="owner" else (0,0)
            dest=root/"config/client"/(key+".pgpass") if key=="owner" else root/"config/private/db-secrets"/(key+".pgpass")
            write_new(dest,entry.encode(),0o600,*owner)
    print("PASS: new isolated migration directories prepared; no container started")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--approved-local-prepare",action="store_true")
    p.add_argument("--mode",choices=("isolated","shared"),default="isolated")
    a=p.parse_args()
    if not a.approved_local_prepare or os.geteuid()!=0:
        raise PreparationError("LOCAL_ROOT_APPROVAL_REQUIRED")
    account=pwd.getpwnam("ashare-migrate")
    if account.pw_uid<100 or account.pw_shell not in ("/usr/sbin/nologin","/sbin/nologin","/bin/false"):
        raise PreparationError("NONINTERACTIVE_ACCOUNT_REQUIRED")
    prepare(ROOT,account.pw_uid,account.pw_gid,a.mode)

if __name__=="__main__":
    try: main()
    except PreparationError as e:
        print("FAILED:",str(e),file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as e:
        print("FAILED:",type(e).__name__,file=sys.stderr)
        raise SystemExit(1) from None
