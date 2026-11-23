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

test("Net-Net is the default while explicit URL selection remains navigable",()=>{
  const sw=read("components/strategy-switch.tsx");
  assert.match(sw,/findStrategy\(id \?\? ""\)\?\.id \?\? "netnet_v1"/);
  assert.doesNotMatch(sw,/sessionStorage\.getItem/);
  const nav=read("components/nav.tsx");
  assert.match(nav,/href === "\/methodology"/);
  assert.doesNotMatch(nav,/sessionStorage\.getItem/);
});

test("screening methods are per strategy and carry explicit research limitations",()=>{
  const page=read("app/methodology/page.tsx");
  const method=read("components/strategy-methodology.tsx");
  assert.match(page,/<StrategySwitch mode="methodology"/);
  for(const id of ["netnet_v1","ma_trend_research_v0","oversold_reversion_v0","quality_momentum_v0"]){
    assert.ok(method.includes(id),"Missing strategy guide: "+id);
  }
  assert.match(method,/策略尚未正式实现|研究规则|计划开发|尚未/);
});

test("loading and screen errors do not pretend the saved Net-Net results are zero",()=>{
  const data=read("lib/data.ts");
  const page=read("app/page.tsx");
  assert.match(data,/screeningRowsStatus/);
  assert.match(data,/不能将其视为筛选结果为零/);
  assert.match(page,/scan\.error/);
  assert.match(page,/历史筛选结果暂不可用/);
});

test("route and strategy change animations respect reduced-motion",()=>{
  assert.match(read("app/template.tsx"),/route-transition/);
  const css=read("app/globals.css");
  assert.match(css,/@keyframes route-fade-in/);
  assert.match(css,/@keyframes strategy-fade-in/);
  assert.match(css,/prefers-reduced-motion/);
});

test("Vercel read-only results use the Neon HTTPS driver, never raw PostgreSQL TCP",()=>{
  const data=read("lib/data.ts");
  const pkg=JSON.parse(read("package.json"));
  assert.equal(typeof pkg.dependencies["@neondatabase/serverless"],"string");
  assert.equal(pkg.dependencies.postgres,undefined);
  assert.match(data,/import \{ neon \} from "@neondatabase\/serverless"/);
  assert.match(data,/neon\(url, \{ arrayMode: false, fullResults: false \}\)/);
  assert.match(data,/process\.env\.DATABASE_READ_URL/);
  assert.doesNotMatch(data,/postgres\(url|process\.env\.DATABASE_URL\b/);
});

test("Value Radar branding uses text only on desktop and mobile", () => {
  const nav = read("components/nav.tsx");
  assert.match(nav,/<strong>价值雷达<\/strong>/);
  assert.match(nav,/className="mobile-brand"[^>]*>价值雷达<\/Link>/);
  assert.doesNotMatch(nav,/className="brand-icon"/);
  assert.doesNotMatch(nav,/<Radar size=\{20\}/);
});

test("stock lookup filters on every keystroke, ranks exact and prefix codes first", () => {
  const search = read("components/screener-results.tsx");
  assert.match(search,/onChange=\{e => updateSearch\(e\.target\.value\)\}/);
  assert.match(search,/if \(code === needle\) return 0/);
  assert.match(search,/if \(name === needle\) return 1/);
  assert.match(search,/if \(code\.startsWith\(needle\)\) return 2/);
  assert.match(search,/if \(name\.startsWith\(needle\)\) return 3/);
  assert.match(search,/const filtered = needle \? matches : tabRows/);
  assert.match(search,/const suggestions = matches\.slice\(0, 8\)/);
  assert.match(search,/role="combobox"/);
  assert.match(search,/role="listbox"/);
  assert.match(search,/role="option"/);
  assert.match(search,/event\.key === "ArrowDown"/);
  assert.match(search,/event\.key === "Enter"/);
  assert.match(search,/onClick=\{\(\) => chooseSuggestion\(row\.ts_code\)\}/);
  assert.match(search,/当前选项卡/);
  assert.doesNotMatch(search,/fetch\(|router\.refresh\(|router\.push\(/);
});

test("live suggestions respect the current tab and thresholds and show no industry",()=>{
  const search = read("components/screener-results.tsx");
  assert.match(search,/const tabRows = useMemo\(\(\) => rows\.filter/);
  assert.match(search,/if \(view === "strict"\) return row\.is_candidate/);
  assert.match(search,/if \(view === "gaps"\) return row\.status === "DATA_GAP"/);
  assert.match(search,/if \(row\.price !== null && row\.price > maxPrice\) return false/);
  assert.match(search,/if \(row\.pb !== null && row\.pb > maxPb\) return false/);
  assert.match(search,/return tabRows\.map\(\(row, index\)/);
  assert.match(search,/const filtered = needle \? matches : tabRows/);
  assert.match(search,/aria-label=\{\x60\$\{viewLabel\}股票搜索结果\x60\}/);
  assert.match(search,/className="stock-suggestion-name"/);
  assert.match(search,/className="stock-suggestion-code"/);
  const dropdown=search.slice(search.indexOf('className="stock-suggestions">'),search.indexOf('        </div>\n        <select aria-label="最高价格"'));
  assert.doesNotMatch(dropdown,/row\.industry/);
  const css=read("app/globals.css");
  assert.match(css,/\.stock-suggestions\{position:absolute/);
  assert.match(css,/\.stock-suggestion\{display:grid/);
});

test("Git pushes do not automatically deploy; manually select the release commit",()=>{
  const config=JSON.parse(read("vercel.json"));
  assert.equal(config.git.deploymentEnabled,false);
});

test("strategy selector has compact spacing without hiding its different parameters",()=>{
  const component=read("components/strategy-switch.tsx");
  const css=read("app/globals.css");
  assert.match(component,/selected\.parameters\.map/);
  assert.match(component,/selected\.requirements\.map/);
  assert.match(css,/\.strategy-switch-panel\{padding:16px 20px;margin-bottom:14px;/);
  assert.match(css,/\.strategy-switch-options\{display:grid;grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(css,/\.strategy-switch-details\{display:flex;flex-wrap:nowrap;/);
  assert.match(css,/overflow-x:auto;overscroll-behavior-x:contain/);
});

test("review table rows stay compact and reasons display two entries plus a correct extra count",()=>{
  const page=read("app/signals/page.tsx");
  const css=read("app/globals.css");
  assert.match(page,/review\.reasons\.slice\(0,2\)/);
  assert.match(page,/review\.reasons\.length > 2/);
  assert.match(page,/review\.reasons\.length-2/);
  assert.match(page,/title=\{review\.reasons\.map\(reason => reason\.label\)\.join/);
  assert.match(css,/\.review-table tbody tr\{height:74px;\}/);
  assert.match(css,/\.review-table tbody td\{height:74px;padding:9px 12px;\}/);
  assert.match(css,/grid-template-rows:repeat\(2,20px\)/);
  assert.match(css,/\.review-more\{grid-column:2;grid-row:2;/);
});
