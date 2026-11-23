const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");

const dir=path.join(__dirname,"..");
const catalog=JSON.parse(fs.readFileSync(path.join(dir,"strategies/catalog.json"),"utf8"));
const file=p=>fs.readFileSync(path.join(dir,p),"utf8");

test("strategy registry has unique stable IDs and requires declared data",()=>{
  assert.ok(catalog.length>=4);
  const ids=catalog.map(x=>x.id);
  assert.equal(new Set(ids).size,ids.length);
  for(const row of catalog){
    assert.match(row.id,/^[a-z0-9_]+$/);
    assert.ok(row.version);
    assert.ok(["live","research","planned"].includes(row.status));
    assert.ok(Array.isArray(row.parameters));
    assert.ok(row.requirements.length>0);
    for(const p of row.parameters){
      assert.ok(p.min<=p.default && p.default<=p.max);
    }
  }
});

test("only Net-Net can claim current daily live status",()=>{
  assert.deepEqual(catalog.filter(x=>x.status==="live").map(x=>x.id),["netnet_v1"]);
  assert.equal(catalog.find(x=>x.id==="ma_trend_research_v0").status,"research");
});

test("catalog is used by strategy center, detail page and backtest experiment page",()=>{
  assert.match(file("lib/strategies.ts"),/catalogJson/);
  assert.match(file("app/strategies/page.tsx"),/strategies\.map/);
  assert.match(file("app/strategies/[id]/page.tsx"),/findStrategy/);
  assert.match(file("app/backtest/page.tsx"),/strategies\.map/);
  assert.match(file("components/nav.tsx"),/href: "\/strategies"/);
});

test("parameter editor is explicitly a non-executing draft",()=>{
  const editor=file("components/strategy-draft.tsx");
  assert.match(editor,/draft_not_executable/);
  assert.match(editor,/setValues/);
  assert.match(editor,/navigator\.clipboard\.writeText/);
  assert.match(editor,/不写入数据库/);
});

test("production homepage still labels the running Net-Net strategy",()=>{
  assert.match(file("app/page.tsx"),/Net-Net每日选股/);
  assert.match(file("app/backtest/page.tsx"),/尚未具备可正式发布的Net-Net历史收益数据/);
});
