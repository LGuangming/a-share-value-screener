import "server-only";
import { neon } from "@neondatabase/serverless";
import { Pool, types, type QueryResultRow } from "pg";

export type DatabaseDriver = "neon" | "postgres";
type Query = (strings: TemplateStringsArray, ...values: unknown[]) => Promise<QueryResultRow[]>;
type Reader = { query: Query; close: () => Promise<void> };

// Unknown values fail closed rather than silently reaching another database.
export function resolveDriver(value: string | undefined): DatabaseDriver {
  if (value === undefined || value === "" || value === "neon") return "neon";
  if (value === "postgres") return value;
  throw new Error("Invalid DATABASE_DRIVER");
}

// Only static SQL text is concatenated. Every interpolation is a bind value.
export function parameterize(strings: TemplateStringsArray, values: unknown[]) {
  if (strings.length !== values.length + 1) throw new Error("Invalid SQL template");
  const text = strings.reduce((sql, part, i) => sql + (i ? "$" + i : "") + part, "");
  return { text, values };
}

export function createReader(url: string, driver: DatabaseDriver): Reader {
  if (driver === "neon") {
    // Preserve HTTPS for Vercel, where TCP connections previously timed out.
    const sql = neon(url, { arrayMode: false, fullResults: false });
    return { query: async (strings, ...values) => sql(strings, ...values), close: async () => {} };
  }
  if (driver !== "postgres") throw new Error("Invalid DATABASE_DRIVER");
  const pool = new Pool({
    connectionString: url,
    max: 4,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
    statement_timeout: 15_000,
    options: "-c default_transaction_read_only=on -c TimeZone=UTC",
    // DATE is a calendar day, not a local-midnight instant. Preserve it across
    // server TZ changes; leave numeric, bigint, JSONB and timestamps to pg.
    types: {
      getTypeParser: (oid, format) => oid === 1082 && format !== "binary"
        ? (value: string) => value
        : format === "binary" ? types.getTypeParser(oid, "binary") : types.getTypeParser(oid, "text"),
    },
  });
  // An idle connection failure must not crash the Next.js process.
  pool.on("error", () => console.error("PostgreSQL idle reader connection failed"));
  return {
    query: async (strings, ...values) => (await pool.query(parameterize(strings, values))).rows,
    close: () => pool.end(),
  };
}

let reader: Reader | null = null;

// Process-scoped configuration. Restart the app when switching drivers or URLs.
// Never use DATABASE_URL, even if DATABASE_READ_URL is missing or inaccessible.
export function connection(): Query | null {
  const url = process.env.DATABASE_READ_URL;
  if (!url) return null;
  if (!reader) reader = createReader(url, resolveDriver(process.env.DATABASE_DRIVER));
  return reader.query;
}
