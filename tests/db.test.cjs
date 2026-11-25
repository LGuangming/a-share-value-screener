const test=require("node:test"),assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),ts=require("typescript");
const {Pool}=require("pg");
const {randomBytes}=require("node:crypto");
const root=path.join(__dirname,"..");
function load(name, overrides={}){
  const m={exports:{}};
  const source=fs.readFileSync(path.join(root,"lib",name+".ts"),"utf8");
  const resolve=id=>id==="server-only"?{}:Object.hasOwn(overrides,id)?overrides[id]:require(id);
  new Function("module","exports","require",ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText)(m,m.exports,resolve);
  return m.exports;
}
const db=load("db");
test("Neon remains default, explicit PostgreSQL supported, unknown driver rejected",()=>{
  assert.equal(db.resolveDriver(undefined),"neon");
  assert.equal(db.resolveDriver(""),"neon");
  assert.equal(db.resolveDriver("neon"),"neon");
  assert.equal(db.resolveDriver("postgres"),"postgres");
  assert.throws(()=>db.resolveDriver("unknown"),/Invalid DATABASE_DRIVER/);
});
test("template interpolations become positional parameters, not SQL",()=>{
  const attack="x'; DROP TABLE stocks; --";
  const result=db.parameterize(["SELECT ","::text, ","::text"],[attack,"safe"]);
  assert.deepEqual(result,{text:"SELECT $1::text, $2::text",values:[attack,"safe"]});
  assert.deepEqual(db.parameterize(["SELECT 1"],[]),{text:"SELECT 1",values:[]});
  assert.throws(()=>db.parameterize(["SELECT "],[1]),/Invalid SQL template/);
});
test("default reader invokes Neon HTTP with unchanged template and never falls back to writer",async()=>{
  const old={read:process.env.DATABASE_READ_URL,write:process.env.DATABASE_URL,driver:process.env.DATABASE_DRIVER};
  const restore=(k,v)=>v===undefined?delete process.env[k]:process.env[k]=v;
  let calls=[];
  const m=load("db",{"@neondatabase/serverless":{neon:(url,opts)=>{
    calls.push({url,opts});return async(strings,...values)=>{calls.push({strings:[...strings],values});return [{answer:42}];};
  }}});
  try{
    delete process.env.DATABASE_READ_URL;delete process.env.DATABASE_DRIVER;
    process.env.DATABASE_URL="postgresql://writer.invalid/db";
    assert.equal(m.connection(),null);assert.equal(calls.length,0);
    process.env.DATABASE_READ_URL="postgresql://reader.invalid/db";
    const query=m.connection();
    assert.deepEqual(await query`SELECT ${42}`,[{answer:42}]);
    assert.deepEqual(calls,[{url:"postgresql://reader.invalid/db",opts:{arrayMode:false,fullResults:false}},{strings:["SELECT ",""],values:[42]}]);
    assert.equal(m.connection(),query);
  }finally{
    restore("DATABASE_READ_URL",old.read);restore("DATABASE_URL",old.write);restore("DATABASE_DRIVER",old.driver);
  }
});

test("real PostgreSQL: restricted reader, parameterized queries and application results",{skip:!process.env.TEST_POSTGRES_URL},async()=>{
  const adminUrl=new URL(process.env.TEST_POSTGRES_URL);
  assert.equal(adminUrl.hostname,"127.0.0.1","integration tests require a disposable loopback database");
  assert.equal(adminUrl.pathname,"/ashare_test");
  assert.equal(adminUrl.username,"fixture_admin");
  const admin=new Pool({connectionString:adminUrl.href,max:1});
  const password=randomBytes(20).toString("hex");
  const readerUrl=new URL(adminUrl);readerUrl.username="fixture_reader";readerUrl.password=password;
  let reader;
  try{
    await admin.query(fs.readFileSync(path.join(root,"database","neon-schema.sql"),"utf8"));
    await admin.query("CREATE ROLE fixture_reader LOGIN PASSWORD '"+password+"'");
    await admin.query("GRANT CONNECT ON DATABASE ashare_test TO fixture_reader; GRANT USAGE ON SCHEMA public TO fixture_reader; GRANT SELECT ON ALL TABLES IN SCHEMA public TO fixture_reader");
    await admin.query(`
      INSERT INTO stocks(ts_code,name,industry,market,list_date) VALUES('000001.SZ','Fixture Bank','Bank','Main','1991-04-03');
      INSERT INTO daily_prices(ts_code,trade_date,close,pb,turnover_rate,market_cap_yuan,raw) VALUES('000001.SZ','2025-01-03',10.25,0.5,2,100000,'{"pe_ttm":4}');
      INSERT INTO price_history(ts_code,trade_date,close) VALUES('000001.SZ','2025-01-02',9),('000001.SZ','2025-01-03',9.5);
      INSERT INTO financial_reports(ts_code,kind,end_date,disclosed_at,report_type,metrics,raw) VALUES('000001.SZ','balancesheet','2024-09-30','2024-10-30','1','{}','{"total_assets":100,"total_liab":40}');
      INSERT INTO job_runs(run_id,job_type,trade_date,finished_at,status,metrics) VALUES('00000000-0000-4000-8000-000000000001','daily_scan','2025-01-03','2025-01-03T12:00:00Z','success','{"quoted_stocks":1}');
      INSERT INTO screen_results(trade_date,ts_code,run_id,price,pb,ncav_coverage,is_candidate,status,signal,flags,assessment) VALUES('2025-01-03','000001.SZ','00000000-0000-4000-8000-000000000001',10.25,0.5,1.5,true,'CANDIDATE','NOT_EVALUATED','["fixture"]','fixture');
    `);
    reader=db.createReader(readerUrl.href,"postgres");
    const q=reader.query;
    const [{day,n,flag,doc,ro,can_write}]=await q`SELECT '2025-01-03'::date AS day, 12.34::numeric AS n, true AS flag, '{"x":1}'::jsonb AS doc, current_setting('transaction_read_only') AS ro, has_table_privilege('stocks','UPDATE') AS can_write`;
    assert.equal(day,"2025-01-03");assert.equal(Number(n),12.34);assert.equal(flag,true);assert.deepEqual(doc,{x:1});assert.equal(ro,"on");assert.equal(can_write,false);
    const attack="x'; DROP TABLE stocks; --";
    assert.deepEqual(await q`SELECT ${attack}::text AS value`,[{value:attack}]);
    await assert.rejects(q`UPDATE stocks SET name='forbidden'`,/read-only|permission denied/i);
    const company=load("company-research");
    const data=load("data",{"./db":{connection:()=>q},"next/cache":{unstable_cache:fn=>fn},"./company-research":company});
    assert.deepEqual(await data.searchStockIdentities("Fixture"),[{ts_code:"000001.SZ",name:"Fixture Bank",industry:"Bank"}]);
    assert.deepEqual(await data.searchStockIdentities(attack),[]);
    assert.equal((await data.latestSnapshot()).snapshot.trade_date,"2025-01-03");
    const rows=await data.screeningRows("2025-01-03");
    assert.equal(rows.length,1);assert.equal(rows[0].price,10.25);assert.deepEqual(rows[0].flags,["fixture"]);assert.equal(rows[0].cashflow_detail,"REPORT_MISSING");
    const detail=await data.stockDetail("000001.SZ");
    assert.deepEqual(detail.prices,[{day:"2025-01-02",close:9},{day:"2025-01-03",close:10.25}]);
    const overview=await data.companyOverview("000001.SZ");
    assert.equal(overview.overview.listDate,"1991-04-03");assert.equal(overview.overview.balance.equity,60);assert.equal(overview.overview.market.peTtm,4);
    assert.equal((await data.dataCenterSnapshot()).rows,1);
    assert.deepEqual(await data.publishedCandidateDates(),["2025-01-03"]);
    assert.equal((await data.comparisonDirectory()).stocks.length,1);
  }finally{if(reader)await reader.close();await admin.end();}
});
