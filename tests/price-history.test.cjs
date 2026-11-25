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
  const neonReader=fs.readFileSync(path.join(__dirname,"../lib/archive-neon.ts"),"utf8");
  assert.match(neonReader, /NEON_STORAGE_READ_ACCESS_KEY_ID/);
  assert.doesNotMatch(neonReader, /PutObjectCommand|DeleteObjectCommand/);
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

test("continuous archive merge sorts and rejects duplicate or incomplete bars",()=>{
  const {mergeArchivedBars}=barModule.exports;
  const a={day:"2024-12-31",open:10,high:11,low:9,close:10,volume:12},b={...a,day:"2025-01-02"};
  assert.deepEqual(mergeArchivedBars([[b],[a]]),[a,b]);
  assert.throws(()=>mergeArchivedBars([[a],[a]]),/Invalid archived/);
  assert.throws(()=>mergeArchivedBars([[{day:a.day,close:10}]]),/Invalid archived/);
});
test("adjustment anchors across years and refuses missing or duplicate factors",()=>{
  const {adjustBars}=barModule.exports;
  const a={day:"2024-12-31",open:10,high:12,low:9,close:10,volume:200,amount:1000},b={...a,day:"2025-01-02",open:5,high:6,low:4.5,close:5};
  const factors=[{day:a.day,factor:1},{day:b.day,factor:2}];
  assert.equal(adjustBars([a,b],factors,"forward")[0].close,5);
  assert.equal(adjustBars([a,b],factors,"backward")[1].close,10);
  assert.equal(adjustBars([a,b],factors,"forward")[0].volume,200);
  assert.equal(adjustBars([a,b],factors,"forward")[0].amount,1000);
  assert.throws(()=>adjustBars([a,b],factors.slice(0,1),"forward"),/覆盖不完整/);
  assert.throws(()=>adjustBars([a,b],[...factors,factors[0]],"forward"),/Duplicate/);
  assert.equal(adjustBars([a,b],[],"unadjusted")[0].close,10);
});
test("indicators use genuine OHLC and omit insufficient lookback",()=>{
  const {macd,rsi,boll,kdj}=barModule.exports;
  const flat=Array.from({length:40},(_,i)=>({day:String(i),open:10,high:10,low:10,close:10}));
  assert.deepEqual(macd(flat)[0],{time:"33",dif:0,dea:0,histogram:0});
  assert.equal(rsi(flat)[0].value,50);
  assert.equal(rsi(flat.map((b,i)=>({...b,close:10+i})))[0].value,100);
  assert.equal(rsi(flat.map((b,i)=>({...b,close:50-i})))[0].value,0);
  assert.deepEqual(boll(flat)[0],{time:"19",middle:10,upper:10,lower:10});
  assert.deepEqual(kdj(flat)[0],{time:"8",k:50,d:50,j:50});
  assert.deepEqual(kdj(flat.map(b=>({day:b.day,close:b.close}))),[]);
  assert.deepEqual(macd(flat.slice(0,20)),[]);
});
test("V4 chart requests yearly archives in bounded batches and checks factor integrity",()=>{
  assert.match(chart,/i<years.length;i\+=2/);
  assert.match(chart,/mergeArchivedBars\(all\)/);
  assert.match(chart,/adjustBars\(rawDaily,factors.values,adjustment\)/);
  assert.match(archive,/Factor checksum mismatch/);
  assert.match(archive,/Factor manifest totals mismatch/);
  assert.match(archive,/Factor row count mismatch/);
  assert.match(chart,/disabled=\{loading\|\|!archive.length\}/);
});

 test("current-year chart uses incremental verified month reader",()=>{assert.match(chart,/CURRENT_YEAR/);assert.match(archive,/verified_month_reader/);assert.match(archive,/Incremental checksum mismatch/);assert.match(chart,/coverageNote/);});

test("recent default uses archive endpoints and enables adjustment",()=>{
  assert.match(chart,/useState\("recent"\)/);
  assert.match(chart,/\?range=recent/);
  assert.match(chart,/factors\?range=recent&from=/);
  assert.match(chart,/const rawDaily=archive/);
  assert.doesNotMatch(chart,/source==="recent"\?prices:archive/);
  assert.match(archive,/sessions.size<RECENT_SESSIONS/);
});
test("recent window follows market sessions across years without inventing suspended bars",()=>{
  const {recentWindow,recentMonthRange}=barModule.exports;
  const dates=Array.from({length:130},(_,i)=>new Date(Date.UTC(2025,9,1+i)).toISOString().slice(0,10));
  const bars=dates.filter((_,i)=>i!==100).map(day=>({day,open:10,high:11,low:9,close:10,volume:100,amount:200}));
  const result=recentWindow(bars,dates);
  assert.equal(result.dates.length,120);
  assert.equal(result.bars.length,119);
  assert.equal(result.from,dates[10]);
  assert.equal(result.to,dates.at(-1));
  assert.equal(result.bars[0].day,dates[10]);
  assert.deepEqual(recentMonthRange("2025-12-30","2026-02-02"),[{year:2025,month:"12"},{year:2026,month:"01"},{year:2026,month:"02"}]);
  assert.throws(()=>recentMonthRange("2026-02-30","2026-03-01"));
  const next=new Date(Date.UTC(2025,9,131)).toISOString().slice(0,10);
  const shifted=recentWindow([...bars,{day:next,open:10,high:11,low:9,close:10}], [...dates,next]);
  assert.equal(shifted.from,dates[11]);
  assert.equal(shifted.to,next);
});
