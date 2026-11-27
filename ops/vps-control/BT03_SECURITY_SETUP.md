# BT03 security-only maintenance setup

The accepted historical security builder consumes a read-only market partition
and the frozen stock_basic snapshot, validates the market receipt/hash/row
counts, and produces independent security receipts. Existing market partitions
need not be rebuilt. V2.2's maintenance runner now accepts an explicit
existing_market in a locally approved plan; only security batches are allowed
in this mode. Output stays in the research worker's private work directory.

The installed V2.2 bridge/worker protocol does not change. After CI passes
at the exact maintenance patch revision, a local administrator can install
only maintenance_runner.py (root-owned 0644) while no task is active.
No change to token, existing state, service units or credentials.

Use the accepted BT03 source commit
cd572fb024d11c6a3c8a23198c0ea4cdcdebc085 separately from the bridge source.
The approved CI worker lock is for CPython 3.13 Linux x86_64. Use the exact
DuckDB pin in that lock; do not claim the native host wheel is the CI wheel
unless its hash matches. A native venv with only the required DuckDB wheel
must undergo its own actual acceptance. Source paths must be root-owned,
non-symlink and non-group/world-writable. No Docker group or sudo for
the research worker. The archive mount and market partitions are read-only
in the worker systemd sandbox; only the frozen snapshot is necessary for
historical security builds. Never grant the bridge or worker broad access
to unrelated production credentials.

First use 2025-01 as a control against the existing acceptance result, then
complete the remaining 71 months. Select a new dataset identity; do not
overwrite the accepted 2025-01 security data. Each remote bt03-maintain task
runs one month, with durable completed-batch checkpoint. The output is
provisional historical eligibility evidence, not a certified universe.
2026 incremental eligibility is outside this plan.
