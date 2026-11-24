const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const data = fs.readFileSync(path.join(__dirname, "../lib/data.ts"), "utf8");
const stockPage = fs.readFileSync(path.join(__dirname, "../app/stocks/[code]/page.tsx"), "utf8");
const chart = fs.readFileSync(path.join(__dirname, "../components/market-chart.tsx"), "utf8");
const archive = fs.readFileSync(path.join(__dirname, "../lib/archive-quotes.ts"), "utf8");
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

test("chart uses merged closes and directly reads verified monthly archives", () => {
  assert.match(stockPage, /<MarketChart[^\n]*prices=\{prices\}/);
  assert.match(chart, /CandlestickSeries/);
  assert.match(chart, /LineSeries/);
  assert.match(chart, /bars\.every\(completeBar\)/);
  assert.match(chart, /createSeriesMarkers/);
  assert.match(archive, /parquetReadObjects/);
  assert.match(archive, /createHash\("sha256"\)/);
  assert.match(archive, /NEON_STORAGE_READ_ACCESS_KEY_ID/);
  assert.doesNotMatch(archive, /PutObjectCommand|DeleteObjectCommand/);
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
