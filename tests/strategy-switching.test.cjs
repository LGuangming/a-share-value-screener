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
  assert.match(s,/尚未发布\{mode === "screen"/);
  assert.match(read("app/signals/page.tsx"),/Net-Net候选股票复核清单/);
});

test("screener tab changes and filtering are client-only and paginated",()=>{
  const s=read("components/screener-results.tsx");
  assert.match(s,/"use client"/);
  assert.match(s,/useMemo/);
  assert.match(s,/setView\(/);
  assert.match(s,/aria-selected=\{view === key\}/);
  assert.doesNotMatch(s,/window\.history\.replaceState/);
  assert.match(s,/Filter views and keystrokes are local-only UI state/);
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
  const loading = read("app/loading.tsx");
  assert.match(loading,/route-loading-indicator/);
  assert.doesNotMatch(loading,/A-SHARE RESEARCH WORKSPACE/);
  assert.doesNotMatch(loading,/正在加载研究数据/);
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
  assert.match(data,/股票筛选结果暂不可用/);
  assert.match(page,/scan\.error/);
  assert.match(page,/筛选结果暂不可用/);
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

test("V2 strategy selector uses compact tabs with expandable strategy parameters",()=>{
  const component=read("components/strategy-switch.tsx");
  const css=read("app/theme-orange.css");
  assert.match(component,/selected\.parameters\.map/);
  assert.match(component,/selected\.requirements\.map/);
  assert.match(component,/<details className="strategy-context"/);
  assert.match(component,/<summary>策略参数与数据条件<\/summary>/);
  assert.match(css,/\.strategy-switch-options \{display:flex;flex-wrap:nowrap;overflow-x:auto/);
  assert.match(css,/\.strategy-context\[open\]>\.strategy-context-body \{animation:/);
});

test("review table displays as many complete reasons as fit on two lines",()=>{
  const page=read("app/signals/page.tsx");
  const client=read("components/review-reasons.tsx");
  const css=read("app/theme-orange.css");
  assert.match(page,/import ReviewReasons from/);
  assert.match(page,/<ReviewReasons reasons=\{review\.reasons\.map/);
  assert.match(client,/new ResizeObserver\(update\)/);
  assert.match(client,/if \(line > 2\) return false/);
  assert.match(client,/const hiddenCount = reasons\.length - visibleCount/);
  assert.match(client,/另有\{hiddenCount\}项/);
  assert.match(client,/title=\{reasons\.map\(reason => reason\.label\)\.join/);
  assert.match(css,/\.review-table tbody tr\{height:72px;\}/);
  assert.match(css,/\.review-reasons \{/);
  assert.match(css,/gap:2px 4px/);
  assert.match(css,/\.review-table th:nth-child\(6\)\{width:auto;\}/);
});

test("V2 strategy center uses an accessible research table",()=>{
  const page=read("app/strategies/page.tsx");
  assert.match(page,/className="results-table strategy-library-table"/);
  assert.match(page,/<thead>/);
  assert.match(page,/strategies\.map/);
  assert.match(page,/strategyStatusLabel\[s\.status\]/);
  assert.match(page,/s\.requirements\.map/);
});

test("V2 orange accent is separate from semantic risk colors",()=>{
  const theme=read("app/theme-orange.css");
  const layout=read("app/layout.tsx");
  assert.match(layout,/import "\.\/theme-orange\.css"/);
  assert.match(theme,/--accent:#D04A02;/);
  assert.match(theme,/--green:#237551;/);
  assert.match(theme,/--orange:#EB8C00;/);
  assert.match(theme,/\.nav-link\.is-active/);
  assert.match(theme,/\.review-conclusion-pass/);
  assert.match(theme,/\.review-conclusion-block/);
  assert.match(theme,/prefers-reduced-motion:reduce/);
  assert.match(theme,/\.market-panel \.results-table thead th \{position:sticky/);
});

test("screener view switches never update Next.js searchParams or remount the loading fallback",()=>{
  const screen = read("components/screener-results.tsx");
  const loader = read("app/loading.tsx");
  const css = read("app/globals.css");
  assert.match(screen,/function setFilter\(_param: string, _value: string, update: \(\) => void\)/);
  assert.match(screen,/onClick=\{\(\) => setFilter\("view",key,\(\)=>setView\(key\)\)\}/);
  assert.doesNotMatch(screen,/window\.history\.replaceState/);
  assert.match(loader,/route-loading-indicator/);
  assert.doesNotMatch(loader,/A-SHARE RESEARCH WORKSPACE/);
  assert.match(css,/\.route-loading-indicator\{position:fixed/);
});

test("V2 page controls preserve local filtering and use actual research data",()=>{
  const screen=read("components/screener-results.tsx");
  const stock=read("app/stocks/[code]/page.tsx");
  const backtest=read("app/backtest/page.tsx");
  const config=read("components/backtest-config.tsx");
  assert.match(screen,/function sortBy/);
  assert.match(screen,/aria-sort=/);
  assert.match(screen,/function exportRows/);
  assert.match(screen,/filtered\.map/);
  assert.match(screen,/if\(av===null\)/);
  assert.match(stock,/ResearchTabs/);
  assert.match(stock,/stock\.ncav_yuan/);
  assert.match(backtest,/BacktestStrategyPanel/);
  assert.match(read("components/backtest-strategy-panel.tsx"),/<BacktestConfig strategy=\{active\}/);
  assert.match(backtest,/暂无已发布的历史回测绩效/);
  assert.match(config,/draft_not_executable/);
  assert.match(config,/disabled>运行回测/);
  assert.doesNotMatch(config,/fetch\(/);
});

test("single-row toolbar keeps export and settings together with unclipped dropdowns",()=>{
  const css=read("app/theme-orange.css");
  const screen=read("components/screener-results.tsx");
  assert.match(css,/\.market-panel>\.filters \{[^}]*flex-wrap:nowrap[^}]*overflow:visible/);
  assert.match(css,/\.market-panel>\.filters>\.filter-form \{[^}]*justify-content:safe flex-end[^}]*min-width:0[^}]*flex-wrap:nowrap[^}]*overflow-x:auto/);
  assert.match(screen,/<ToolbarFlyout anchor=\{searchAnchor\}/);
  assert.match(screen,/<ToolbarFlyout anchor=\{columnAnchor\}/);
  assert.match(read("components/toolbar-flyout.tsx"),/createPortal/);
});
test("strategy summary is a selectable union, not a new strategy or fabricated result",()=>{
  const sw=read("components/strategy-switch.tsx");
  const page=read("app/page.tsx");
  const summary=read("components/strategy-summary.tsx");
  assert.match(sw,/chooseStrategy\("summary"\)/);
  assert.match(sw,/showSummary \? /);
  assert.match(page,/strategy.status !== "live"/);
  assert.match(page,/screeningRowsStatus\(snapshot.trade_date,strategy.id\)/);
  assert.match(summary,/type="checkbox"/);
  assert.match(summary,/命中策略/);
  const ts=require("typescript");
  const m={exports:{}};
  new Function("module","exports",ts.transpileModule(read("lib/strategy-summary.ts"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText)(m,m.exports);
  const row=(code,candidate=true)=>({ts_code:code,name:code,industry:null,price:1,market_cap_yuan:10,is_candidate:candidate});
  const groups=[{id:"a",name:"A",state:"ready",rows:[row("001"),row("002"),row("004",false)]},{id:"b",name:"B",state:"ready",rows:[row("001"),row("003")]},{id:"c",name:"C",state:"unpublished",rows:[row("005")]}];
  const union=m.exports.combineStrategyResults(groups,["a","b","c"]);
  assert.deepEqual(union.map(r=>[r.ts_code,r.hits.length]),[["001",2],["002",1],["003",1]]);
  assert.deepEqual(m.exports.combineStrategyResults(groups,["b"]).map(r=>r.ts_code),["001","003"]);
  assert.deepEqual(m.exports.combineStrategyResults(groups,[]),[]);
});

test("V3 workstation shares actual archive chart and read-only candidate data",()=>{
  const page=read("app/research/page.tsx"),workspace=read("components/research-workspace.tsx");
  const detail=read("app/api/research/[code]/route.ts");
  assert.match(page,/combineStrategyResults/);
  assert.match(page,/screeningRowsStatus/);
  assert.match(workspace,/<MarketChart/);
  assert.match(workspace,/detail\?\.recent\.map/);
  assert.match(detail,/stockDetail\(code\)/);
  assert.match(read("lib/data.ts"),/FROM stocks\s+WHERE strpos/);
  assert.match(read("components/nav.tsx"),/label: "行情研究"/);
});
test("V3 controls persist browser-local preferences, and preview uses a modal",()=>{
  const prefs=read("components/workspace-preferences.tsx"),screen=read("components/screener-results.tsx");
  assert.match(prefs,/localStorage\.setItem/);
  assert.match(prefs,/value-radar-watchlist-v1/);
  assert.match(screen,/value-radar-columns-v3/);
  assert.match(screen,/setPointerCapture/);
  assert.match(screen,/moveColumn/);
  assert.match(screen,/<PreviewButton/);
  assert.match(read("components/stock-preview.tsx"),/showModal\(/);
  assert.match(read("components/global-command.tsx"),/e\.ctrlKey\|\|e\.metaKey/);
  const css=read("app/workspace.css");
  assert.match(css,/data-theme=dark/);
  assert.match(css,/data-density=compact/);
  assert.match(css,/prefers-reduced-motion:reduce/);
});

test("V3 mini trends are opt-in, capped and use the actual close history",()=>{const screen=read("components/screener-results.tsx");assert.match(screen,/useMiniTrends\(trendCodes,mini\)/);assert.match(screen,/slice\(0,25\)/);assert.match(read("lib/data.ts"),/recentMiniTrends/);assert.match(read("app/api/research/trends/route.ts"),/codes.length>25/);assert.match(screen,/watchOnly/);});

test("V3 polish keeps search height stable, preserves tabs, and uses readable strategy columns and review cards",()=>{
  const screen=read("components/screener-results.tsx"),home=read("app/page.tsx"),strategies=read("app/strategies/page.tsx"),signals=read("app/signals/page.tsx"),css=read("app/theme-orange.css");
  assert.doesNotMatch(screen,/匹配 \{matches\.length\} 只 \/ 当前/);
  assert.match(screen,/Net-Net筛选结果 <span className="count-badge" aria-live="polite">/);
  assert.doesNotMatch(home,/Net-Net筛选步骤|数据及交易状态/);
  assert.match(css,/\.market-panel>\.filters>\.tabs \{[^}]*min-width:max-content/);
  assert.match(strategies,/<colgroup>/);
  assert.match(css,/\.strategy-library-table \{table-layout:fixed;min-width:1460px/);
  assert.match(read("components/review-reasons.tsx"),/signal-expand-open">展开明细/);
  assert.match(read("components/review-reasons.tsx"),/signal-expand-close">收起明细/);
  assert.match(signals,/className="signal-reason-card"/);
  assert.match(css,/\.signal-reason-details\[open\] \.signal-expand-close \{display:inline/);
});

test("search keeps table height and anchors suggestions below the input",()=>{
  const screen=read("components/screener-results.tsx"),flyout=read("components/toolbar-flyout.tsx"),css=read("app/theme-orange.css");
  assert.match(screen,/resultsRegion\.current\?\.getBoundingClientRect\(\)\.height/);
  assert.match(screen,/minHeight:needle\?searchHeight:undefined/);
  assert.match(screen,/placement="below"/);
  assert.match(flyout,/placement==="auto"&&below<180/);
  assert.match(css,/\.screener-result-region \{overflow-anchor:none/);
});
test("review labels retain actual severity and compact typography",()=>{
  const page=read("app/signals/page.tsx"),reasons=read("components/review-reasons.tsx"),css=read("app/theme-orange.css");
  assert.match(page,/severity: reason\.severity/);
  assert.match(reasons,/reason\.severity\.toLowerCase\(\)/);
  assert.match(css,/\.review-table th:nth-child\(1\)\{width:112px;\}/);
  assert.match(css,/\.review-table \.signal-reason-card>strong \{[^}]*font-size:12px/);
});
test("backtest strategy switches locally and uses strategy-specific draft fields",()=>{
  const panel=read("components/backtest-strategy-panel.tsx"),config=read("components/backtest-config.tsx");
  assert.match(panel,/value=\{active.id\} onChange=\{e=>choose\(e.target.value\)\}/);
  assert.match(panel,/active\.requirements\.map/);
  assert.match(config,/byStrategy\[strategy.id\]/);
  assert.match(config,/strategy\.parameters\.map/);
  assert.match(config,/needsHolding=strategy.id==="oversold_reversion_v0"/);
  assert.match(config,/Number\(params.ma_fast\)<Number\(params.ma_slow\)/);
  assert.match(config,/draft_not_executable/);
});

function loadV4Module(path){
  const ts=require("typescript"),m={exports:{}};
  new Function("module","exports",ts.transpileModule(read(path),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText)(m,m.exports);
  return m.exports;
}
test("V4 strategy modes exclude unpublished data from claimed intersections",()=>{
  const {filterStrategyResults}=loadV4Module("lib/strategy-summary.ts");
  const row=(ts_code)=>({ts_code,name:ts_code,industry:null,price:1,market_cap_yuan:10,is_candidate:true});
  const groups=[{id:"a",name:"A",state:"ready",rows:[row("1"),row("2"),row("2")]},{id:"b",name:"B",state:"ready",rows:[row("2"),row("3")]},{id:"c",name:"C",state:"unpublished",rows:[]}];
  assert.deepEqual(filterStrategyResults(groups,["a","b","c"],"union").rows.map(x=>x.ts_code),["2","1","3"]);
  assert.deepEqual(filterStrategyResults(groups,["a","b"],"intersection").rows.map(x=>x.ts_code),["2"]);
  assert.deepEqual(filterStrategyResults(groups,["a","b"],"minimum",2).rows.map(x=>x.ts_code),["2"]);
  assert.equal(filterStrategyResults(groups,["a","c"],"intersection").complete,false);
  assert.deepEqual(filterStrategyResults(groups,["a","c"],"intersection").rows,[]);
  assert.deepEqual(filterStrategyResults(groups,[],"intersection").rows,[]);
});
test("V4 financial history uses dated consolidated reports and preserves missing values",()=>{
  const {financialHistory,industryDistribution,reportDate}=loadV4Module("lib/research-analysis.ts");
  const base={end_date:"20241231",f_ann_date:"20250430",report_type:"1"};
  const pts=financialHistory({balancesheet:[{...base,total_assets:200,total_liab:60,total_cur_assets:100},{...base,report_type:"6",total_assets:500},{...base,f_ann_date:"20270101",total_assets:999}],income:[{...base,revenue:40,n_income:0},{...base,f_ann_date:"20250501",revenue:42,n_income:1}],cashflow:[{...base,f_ann_date:null,n_cashflow_act:9}],fina_indicator:[{end_date:"20241231",ann_date:"20250501",roe:5}]}, "2026-11-24");
  assert.equal(pts.length,1);
  assert.equal(pts[0].ncav,40);assert.equal(pts[0].leverage,.3);assert.equal(pts[0].revenue,42);
  assert.equal(pts[0].operatingCash,null);assert.equal(pts[0].roe,5);
  assert.equal(pts[0].inventory,null);assert.equal(reportDate("20250230"),null);
  assert.deepEqual(industryDistribution([{industry:"A"},{industry:"A"},{industry:null}]),[{name:"A",count:2},{name:"未分类",count:1}]);
});
test("V4 archive readers remain bounded, read-only and checksum verified",()=>{
  const source=read("lib/archive-research.ts");
  assert.match(source,/codes_sha256!==digest/);
  assert.match(source,/Financial checksum mismatch/);
  assert.match(source,/maxOutputLength/);
  assert.match(source,/m.status!=="research_not_pit_certified"/);
  assert.doesNotMatch(source,/PutObjectCommand|DeleteObjectCommand/);
  assert.match(source,/unknown>y.rows/);
  assert.match(read("app/data-center/page.tsx"),/Promise.allSettled/);
  assert.match(read("components/filter-presets.tsx"),/localStorage.setItem/);
  assert.match(read("components/compare-selection.tsx"),/v.length<5/);
  assert.match(read("app/stocks/[code]/page.tsx"),/<FinancialHistory/);
});

test("V4 candidate comparison is based on successful published results only",()=>{
  const data=read("lib/data.ts");
  assert.match(data,/export async function publishedCandidateDates/);
  assert.match(data,/FULL OUTER JOIN previous_candidates/);
  assert.match(data,/r.strategy='netnet_v1' AND r.trade_date=\$\{day\} AND r.is_candidate=true/);
  assert.match(read("app/candidate-history/page.tsx"),/publishedCandidateChanges\(day,previous\)/);
  assert.match(read("app/page.tsx"),/href="\/candidate-history"/);
});

test("review toggle shares the two-line chip packing and table headings own sorting",()=>{
  const reasons=read("components/review-reasons.tsx"),signals=read("app/signals/page.tsx"),screen=read("components/screener-results.tsx"),css=read("app/theme-orange.css");
  assert.match(signals,/inlineToggle\/><\/summary>/);
  assert.match(reasons,/if \(toggle\) sizes.push/);
  assert.match(reasons,/signal-expand-measure/);
  assert.match(css,/\.review-reasons-inline-toggle \{min-height:20px;max-height:42px/);
  assert.doesNotMatch(screen,/aria-label="排序字段"/);
  assert.match(screen,/onClick=\{\(\)=>sortBy/);
  assert.match(screen,/aria-sort=/);
});

test("site password gate uses signed, expiring, rotation-bound sessions and rejects unsafe return URLs",()=>{
  const ts=require("typescript"),m={exports:{}};
  new Function("module","exports","require",ts.transpileModule(read("lib/site-access.ts"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText)(m,m.exports,id=>id==="server-only"?{}:require(id));
  const a=m.exports,config={password:"test-only-password-123",secret:"ab".repeat(32)},now=1700000000;
  assert.equal(a.passwordMatches(config.password,config),true);
  assert.equal(a.passwordMatches("wrong",config),false);
  const token=a.issueSession(config,now);
  assert.equal(a.validSession(token,config,now),true);
  assert.equal(a.validSession(token,config,now+a.SESSION_SECONDS),false);
  assert.equal(a.validSession(token,{...config,password:"new-test-password-123"},now),false);
  assert.equal(a.validSession(token,{...config,secret:"cd".repeat(32)},now),false);
  assert.equal(a.validSession(token.slice(0,-1)+(token.endsWith("0")?"1":"0"),config,now),false);
  assert.equal(a.validSession(token,null,now),false);
  assert.equal(a.sessionCookie(token).httpOnly,true);
  assert.equal(a.sessionCookie(token).path,"/");
  for(const path of ["//evil.invalid","/%2fexternal.invalid","/login","/api/quotes","/_next/data","https://evil.invalid","/\\evil.invalid"])assert.equal(a.safeReturnPath(path),"/");
  assert.equal(a.safeReturnPath("/research?code=000001.SZ"),"/research?code=000001.SZ");
});
test("site gate covers routes and APIs, fails closed, and login is isolated from research layout",()=>{
  const proxy=read("proxy.ts"),login=read("app/login/route.ts"),layout=read("app/layout.tsx");
  assert.match(proxy,/matcher:\["\/:path\*"\]/);
  assert.match(proxy,/if\(!config\)return NextResponse.json/);
  assert.match(proxy,/status:401/);
  assert.match(proxy,/validSession\(/);
  assert.match(layout,/validSession\(\(await cookies\(\)\)/);
  assert.match(login,/sameOriginPost/);
  assert.match(login,/response.cookies.set\(sessionCookie\(issueSession\(config\)\)\)/);
  assert.match(login,/type="password"/);
  assert.match(read("app/api/access/logout/route.ts"),/sessionCookie\("",0\)/);
});

test("collapsed navigation fully hides wordmark and displays a dedicated compact icon",()=>{
  const nav=read("components/nav.tsx"),css=read("app/workspace.css");
  assert.match(nav,/className="brand-compact" aria-hidden="true"><Radar/);
  assert.match(nav,/aria-label="价值雷达 · 返回选股中心"/);
  assert.match(css,/\.brand-compact\{display:none\}/);
  assert.match(css,/html\[data-sidebar=collapsed\] \.brand-compact\{display:flex/);
  assert.match(css,/html\[data-sidebar=collapsed\] \.brand-text,[^{}]+\{display:none\}/);
  assert.doesNotMatch(css,/\.brand-text strong\{font-size:0\}|\.brand-text strong:before/);
});

test("site access accepts any six-character password without composition rules",()=>{
  const ts=require("typescript"),m={exports:{}};
  new Function("module","exports","require",ts.transpileModule(read("lib/site-access.ts"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText)(m,m.exports,id=>id==="server-only"?{}:require(id));
  const previousPassword=process.env.SITE_ACCESS_PASSWORD,previousSecret=process.env.SITE_SESSION_SECRET;
  try{
    process.env.SITE_SESSION_SECRET="ab".repeat(32);
    for(const password of ["123456","abcdef","!!!!!!","密码测试六位","😀😀😀😀😀😀"]){
      process.env.SITE_ACCESS_PASSWORD=password;
      const config=m.exports.accessConfig();
      assert.ok(config);
      assert.equal(m.exports.passwordMatches(password,config),true);
    }
    for(const password of ["12345","😀😀😀",""]){
      process.env.SITE_ACCESS_PASSWORD=password;
      assert.equal(m.exports.accessConfig(),null);
    }
  }finally{
    if(previousPassword===undefined)delete process.env.SITE_ACCESS_PASSWORD;else process.env.SITE_ACCESS_PASSWORD=previousPassword;
    if(previousSecret===undefined)delete process.env.SITE_SESSION_SECRET;else process.env.SITE_SESSION_SECRET=previousSecret;
  }
});
