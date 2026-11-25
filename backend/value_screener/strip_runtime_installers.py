"""Build-only diagnostic and removal of optional installer modules.
Never change dpkg metadata or suppress vulnerability scanning.
"""
import importlib.util
import pathlib
import shutil
import sys
MODULES = ("pip", "setuptools", "wheel", "ensurepip")
ROOTS = tuple(pathlib.Path(p).resolve() for p in (
    "/usr/lib/python3.13", "/usr/lib/python3/dist-packages",
    "/usr/local/lib/python3.13", "/usr/local/lib/python3.13/site-packages",
))
def main():
    print("Runtime Python:", sys.version, flush=True)
    for name in MODULES:
        spec = importlib.util.find_spec(name)
        print(f"Installer module {name}: {getattr(spec, 'origin', None)}", flush=True)
        if spec is None:
            continue
        if not spec.origin:
            raise RuntimeError(f"Ambiguous installer module: {name}")
        origin = pathlib.Path(spec.origin)
        target = origin.parent if origin.name == "__init__.py" else origin
        if target.name not in (name, name + ".py") or target.parent.resolve() not in ROOTS or target.is_symlink():
            raise RuntimeError(f"Unexpected installer location: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        print(f"Removed optional installer files: {target}", flush=True)
    # Keep all package metadata, including venv records, for the scanner.
    importlib.invalidate_caches()
    for name in MODULES:
        if importlib.util.find_spec(name) is not None:
            raise RuntimeError(f"Installer still present: {name}")
if __name__ == "__main__":
    main()
