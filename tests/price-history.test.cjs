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

const ts = require("typescript");
const barModule = {exports:{}};
const source = fs.readFileSync(path.join(__dirname,"../lib/market-bars.ts"),"utf8");
new Function("module","exports",ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText)(barModule,barModule.exports);
const {completeBar,aggregateBars,movingAverage}=barModule.exports;
test("weekly aggregation uses first open, last close and genuine range and volume",()=>{
  const a={day:"2025-01-06",open:10,high:11,low:9,close:10.5,volume:100,amount:500};
  const b={day:"2025-01-07",open:10.5,high:12,low:10,close:11,volume:200,amount:700};
  assert.deepEqual(aggregateBars([a,b],"W"),[{day:"2025-01-07",open:10,high:12,low:9,close:11,volume:300,amount:1200}]);
  assert.equal(completeBar({day:b.day,close:b.close}),false);
  assert.deepEqual(aggregateBars([a,{day:b.day,close:11}],"W"),[{day:b.day,close:11}]);
});
test("moving average omits insufficient history and uses actual closes",()=>{
  const bars=[{day:"2025-01-06",close:1},{day:"2025-01-07",close:2},{day:"2025-01-08",close:3}];
  assert.deepEqual(movingAverage(bars,2),[{time:"2025-01-07",value:1.5},{time:"2025-01-08",value:2.5}]);
  assert.deepEqual(movingAverage(bars,5),[]);
});
