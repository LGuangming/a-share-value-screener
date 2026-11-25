"""Extract a hash-verified SQLite release ZIP with bounded Unix modes."""
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

REQUIRED_SCRIPTS = ("configure", "autosetup/autosetup", "autosetup/autosetup-find-tclsh")

def extract_sqlite_source(archive, destination, root_name="sqlite-src-3540100"):
    destination = Path(destination).resolve()
    root = destination / root_name
    if root.exists() or root.is_symlink():
        raise ValueError("Source destination already exists")
    executable = []
    seen = set()
    with zipfile.ZipFile(archive) as z:
        # Validate the entire archive before creating any files.
        for entry in z.infolist():
            p = PurePosixPath(entry.filename)
            parts = p.parts
            if (not parts or parts[0] != root_name or p.is_absolute()
                    or ".." in parts or "\\" in entry.filename
                    or any(":" in part for part in parts)):
                raise ValueError(f"Unsafe ZIP path: {entry.filename}")
            if p in seen:
                raise ValueError(f"Duplicate ZIP path: {entry.filename}")
            seen.add(p)
            mode = entry.external_attr >> 16 if entry.create_system == 3 else 0
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG):
                raise ValueError(f"Unsupported ZIP file type: {entry.filename}")
        for entry in z.infolist():
            p = PurePosixPath(entry.filename)
            path = destination.joinpath(*p.parts)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as src, path.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            mode = entry.external_attr >> 16 if entry.create_system == 3 else 0
            # Only restore execution for regular files marked executable.
            # Strip setuid/setgid/sticky and group/world write bits.
            execute = bool(mode & 0o111)
            path.chmod(0o755 if execute else 0o644)
            if execute:
                executable.append(str(path.relative_to(root)))
    # Some official ZIP producers omit Unix executable metadata. Restore
    # only the known entry point and directly invoked Autosetup helpers.
    for name in REQUIRED_SCRIPTS:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing required build script: {name}")
        with path.open("rb") as src:
            if src.readline(256).rstrip() != b"#!/bin/sh":
                raise ValueError(f"Unexpected build script interpreter: {name}")
        path.chmod(0o755)
        if name not in executable:
            executable.append(name)
    return root, sorted(executable)
