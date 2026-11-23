const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const read=(p)=>fs.readFileSync(path.join(__dirname,"..",p),"utf8");

test("both screener and trading observation use a shared strategy switch",()=>{
  assert.match(read("app/page.tsx"),/<StrategySwitch mode="screen"/);
  assert.match(read("app/signals/page.tsx"),/<StrategySwitch mode="signals"/);
  const sw=read("components/strategy-switch.tsx");
  assert.match(sw,/strategies\.map/);
  assert.match(sw,/selected\.parameters\.map/);
  assert.match(sw,/selected\.requirements\.map/);
  assert.match(sw,/window\.history\.replaceState/);
  assert.match(sw,/netnet_v1/);
  assert.match(sw,/strategy-state/);
});

test("research and planned strategies cannot inherit Net-Net candidate stocks",()=>{
  const s=read("components/strategy-switch.tsx");
  assert.match(s,/selected\.status === "live" && selected\.id === "netnet_v1"/);
  assert.match(s,/hidden=\{!canShowLive\}/);
  assert.match(s,/尚未发布独立的每日/);
  assert.match(s,/不会将 Net-Net 的股票/);
  assert.match(read("app/signals/page.tsx"),/Net-Net候选股票复核清单/);
});

test("screener tab changes and filtering are client-only and paginated",()=>{
  const s=read("components/screener-results.tsx");
  assert.match(s,/"use client"/);
  assert.match(s,/useMemo/);
  assert.match(s,/setView\(/);
  assert.match(s,/aria-selected=\{view === key\}/);
  assert.match(s,/window\.history\.replaceState/);
  assert.match(s,/filtered\.slice\(0,visibleCount\)/);
  assert.match(s,/setVisibleCount\(n=>n\+100\)/);
  assert.doesNotMatch(s,/router\.refresh\(\)|router\.push\(/);
});

test("database queries are strategy-keyed, cached, and still require published scan",()=>{
  const s=read("lib/data.ts");
  assert.match(s,/unstable_cache/);
  assert.match(s,/revalidate: 90/);
  assert.match(s,/r\.strategy=\$\{strategy\}/);
  assert.match(s,/j\.status='success'/);
  assert.match(s,/screeningRows\(day: string, strategy: string/);
  assert.match(read("components/nav.tsx"),/label: "选股中心"/);
  assert.doesNotMatch(read("components/nav.tsx"),/ResizeObserver/);
});

test("loading fallback is present for uncached initial navigation",()=>{
  assert.match(read("app/loading.tsx"),/正在加载研究数据/);
});
