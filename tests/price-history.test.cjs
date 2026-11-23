const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const data = fs.readFileSync(path.join(__dirname, "../lib/data.ts"), "utf8");
const stockPage = fs.readFileSync(path.join(__dirname, "../app/stocks/[code]/page.tsx"), "utf8");
const spark = fs.readFileSync(path.join(__dirname, "../components/sparkline.tsx"), "utf8");
const schema = fs.readFileSync(path.join(__dirname, "../database/neon-schema.sql"), "utf8");
const workflow = fs.readFileSync(path.join(__dirname, "../.github/workflows/recent-price-history.yml"), "utf8");

test("stock detail combines compact historical closes and daily quotes", () => {
  const detail = data.slice(data.indexOf("export async function stockDetail"));
  assert.match(detail, /FROM daily_prices WHERE ts_code=\$\{code\}/);
  assert.match(detail, /FROM price_history WHERE ts_code=\$\{code\}/);
  assert.match(detail, /UNION ALL/);
  assert.match(detail, /SELECT DISTINCT ON \(trade_date\)/);
  assert.match(detail, /ORDER BY trade_date DESC, source_priority DESC/);
  assert.match(detail, /ORDER BY trade_date DESC LIMIT 120/);
  assert.match(detail, /ORDER BY trade_date ASC/);
});

test("history query failures cannot hide an otherwise available stock detail", () => {
  const detail = data.slice(data.indexOf("export async function stockDetail"));
  assert.match(detail, /catch \(historyError\)/);
  assert.match(detail, /Read compact price history failed/);
  assert.match(detail, /return \{ stock: recent\[0\] \?\? null, prices, recent \}/);
});

test("chart is backed by merged history and exposes the loaded session count", () => {
  assert.match(stockPage, /<Sparkline prices=\{prices\}/);
  assert.match(stockPage, /最近 \{prices\.length\} 个交易日收盘价/);
  assert.match(spark, /prices\.length < 2/);
});

test("Neon schema contains the compact price history table", () => {
  assert.match(schema, /create table if not exists price_history \(/);
  assert.match(schema, /primary key\(ts_code, trade_date\)/);
});

test("one-off history workflow defaults to 160 total calendar days", () => {
  assert.match(workflow, /default: '160'/);
  assert.match(workflow, /HISTORY_DAYS:.*'160'/);
  assert.match(workflow, /bootstrap-prices --days/);
});
