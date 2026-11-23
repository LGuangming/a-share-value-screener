"""Read-only Neon/Postgres capacity audit; no schema changes or credentials logged."""
from __future__ import annotations

import json
import os

from .store import Store

FREE_PG_BUDGET_BYTES = 1_000_000_000  # planning reference, not Neon billing meter


def audit(url: str) -> dict:
    with Store(url).connect() as conn:
        overview = conn.execute("""
            SELECT pg_database_size(current_database()) AS database_bytes,
                   current_database() AS database_name
        """).fetchone()
        sizes = conn.execute("""
            SELECT relname AS table_name,
                   pg_total_relation_size(relid) AS total_bytes,
                   pg_relation_size(relid) AS heap_bytes,
                   pg_indexes_size(relid) AS index_bytes
            FROM pg_catalog.pg_statio_user_tables
            WHERE schemaname='public'
            ORDER BY pg_total_relation_size(relid) DESC
        """).fetchall()
        table_rows = []
        for item in sizes:
            table_rows.append({
                "table": item["table_name"],
                "total_bytes": int(item["total_bytes"]),
                "heap_bytes": int(item["heap_bytes"]),
                "index_bytes": int(item["index_bytes"]),
            })
        total = int(overview["database_bytes"])
        return {
            "database_bytes": total,
            "reference_free_limit_bytes": FREE_PG_BUDGET_BYTES,
            "planning_ratio": round(total / FREE_PG_BUDGET_BYTES, 4),
            "tables": table_rows,
            "note": "pg_database_size is PostgreSQL allocation, not the Neon billed quota meter. Verify storage in Neon console.",
        }


def main() -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    result = audit(url)
    print("NEON_CAPACITY_AUDIT " + json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
