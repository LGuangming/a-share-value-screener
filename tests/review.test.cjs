const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../lib/review.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
}).outputText;
const runtime = {exports: {}};
vm.runInNewContext(compiled, runtime, {filename: "review.js"});
const {evaluateFundamentalReview: evaluate} = runtime.exports;

const baseline = {
  is_candidate: true, status: "NET_NET", flags: [],
  cashflow_detail: "VERIFIED", operating_cf_yuan: 100, net_profit_yuan: 20,
};

test("clean candidate passes fixed screening rules only", () => {
  const r = evaluate(baseline);
  assert.equal(r.conclusion, "通过");
  assert.equal(r.severity, "LOW");
  assert.equal(r.priority, "P2");
  assert.equal(r.reasons.length, 0);
});

test("negative cash flow alone is an operating review, not inferred to be permanent", () => {
  const r = evaluate({...baseline, flags:["NEGATIVE_OPERATING_CASHFLOW"], operating_cf_yuan:-1});
  assert.equal(r.conclusion, "待补充资料");
  assert.equal(r.severity, "MEDIUM");
  assert.equal(r.focus, "经营风险");
});

test("negative cash flow and loss together escalate to high urgency", () => {
  const r = evaluate({...baseline, flags:["NEGATIVE_OPERATING_CASHFLOW","LOSS_MAKING"],
    operating_cf_yuan:-1, net_profit_yuan:-1});
  assert.equal(r.severity, "HIGH");
  assert.equal(r.priority, "P0");
  assert.equal(r.conclusion, "不建议进入买入观察");
});

test("stress shortfall blocks buy observation even when NCAV filter passed", () => {
  const r = evaluate({...baseline, flags:["STRESS_COVERAGE_BELOW_100"]});
  assert.equal(r.conclusion, "不建议进入买入观察");
  assert.equal(r.focus, "资产质量");
});

for(const [detail,label] of [
  ["REPORT_MISSING", "现金流量表缺失"],
  ["FIELD_MISSING", "经营现金流关键字段缺失"],
  ["NOT_YET_DISCLOSED", "报表尚未披露（截至筛选日）"],
]) {
  test(`cashflow state ${detail} is separately explained without assigning a zero`, () => {
    const r = evaluate({...baseline, cashflow_detail:detail,
      flags:["CASHFLOW_NOT_VERIFIED"], operating_cf_yuan:null});
    assert.equal(r.conclusion, "待补充资料");
    assert.equal(r.severity, "MEDIUM");
    assert.equal(r.reasons.filter(x => x.code.startsWith("CASHFLOW_")).length, 1);
    assert.equal(r.reasons.find(x => x.code.startsWith("CASHFLOW_")).label, label);
    assert.equal(r.focus, "资料缺失");
  });
}

test("unknown backend flag never becomes an automatic pass", () => {
  const r = evaluate({...baseline, flags:["NEW_UNRECOGNIZED_FLAG"]});
  assert.equal(r.conclusion, "待补充资料");
  assert.equal(r.severity, "MEDIUM");
});

test("missing profitability data cannot be shown as a verified pass", () => {
  const r = evaluate({...baseline, net_profit_yuan:null});
  assert.equal(r.conclusion, "待补充资料");
  assert.ok(r.reasons.some(x => x.code === "PROFIT_NOT_VERIFIED"));
});

test("non-candidate is not assigned a positive screen conclusion", () => {
  const r = evaluate({...baseline, is_candidate:false});
  assert.equal(r.conclusion, "不适用");
});

test("a high risk finding takes priority over data gaps", () => {
  const r = evaluate({...baseline,
    flags:["STRESS_INPUT_INCOMPLETE","STRESS_COVERAGE_BELOW_100"],
    cashflow_detail:"REPORT_MISSING", operating_cf_yuan:null});
  assert.equal(r.severity, "HIGH");
  assert.equal(r.focus, "资产质量");
  assert.equal(r.priority, "P0");
});
