const test=require("node:test"),assert=require("node:assert/strict");
const fs=require("node:fs/promises"),sync=require("node:fs"),os=require("node:os"),path=require("node:path"),ts=require("typescript");
const {execFileSync}=require("node:child_process");
const {createHash}=require("node:crypto");
const {gzipSync}=require("node:zlib");
const {Readable}=require("node:stream");
const root=path.join(__dirname,".."),prefix="a-share-backtest/01_raw_data";
function loader(overrides={}){
  const loaded=new Map();
  function load(name){
    if(loaded.has(name))return loaded.get(name).exports;
    const m={exports:{}};loaded.set(name,m);
    const source=sync.readFileSync(path.join(root,"lib",name+".ts"),"utf8");
    const resolve=id=>id==="server-only"?{}:Object.hasOwn(overrides,id)?overrides[id]:id.startsWith("./")?load(id.slice(2)):require(id);
    new Function("module","exports","require",ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText)(m,m.exports,resolve);
    return m.exports;
  }
  return load;
}
const cacheKeys=[];
const cache={unstable_cache:(fn,keys,opts)=>{cacheKeys.push({keys,opts});return fn;}};
const load=loader({"next/cache":cache});
const storage=load("archive-storage");
const local=load("archive-local");
async function temp(t){
  const dir=await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),"ashare-archive-")));
  t.after(()=>fs.rm(dir,{recursive:true,force:true}));return dir;
}
async function write(dir,key,value){const p=path.join(dir,key);await fs.mkdir(path.dirname(p),{recursive:true});await fs.writeFile(p,value);return p;}
function env(t,values){
  const old=Object.fromEntries(Object.keys(values).map(k=>[k,process.env[k]]));
  for(const [k,v] of Object.entries(values))if(v===undefined)delete process.env[k];else process.env[k]=v;
  t.after(()=>{for(const [k,v] of Object.entries(old))if(v===undefined)delete process.env[k];else process.env[k]=v;});
}
test("storage defaults to Neon; invalid driver and missing local root fail closed",async t=>{
  assert.equal(storage.resolveArchiveDriver(undefined),"neon");
  assert.equal(storage.resolveArchiveDriver(""),"neon");
  assert.equal(storage.resolveArchiveDriver("local"),"local");
  assert.throws(()=>storage.resolveArchiveDriver("s3"),/配置无效/);
  env(t,{ARCHIVE_STORAGE_DRIVER:"local",ARCHIVE_LOCAL_ROOT:undefined});
  assert.throws(()=>storage.storage(),/尚未配置/);
  assert.throws(()=>local.createLocalStorage("/"),/尚未配置/);
  const dir=await temp(t);
  await assert.rejects(local.createLocalStorage(path.join(dir,"missing")).get(prefix+"/x",10),/目录不可用/);
});
test("bounded local read, no path traversal, no symlinks or non-regular objects",async t=>{
  const dir=await temp(t),outside=await temp(t);
  const s=local.createLocalStorage(dir);
  await write(dir,prefix+"/sample.json","abc");
  assert.equal(Buffer.from(await s.get(prefix+"/sample.json",3)).toString(),"abc");
  assert.equal(await s.get(prefix+"/missing",3),null);
  await assert.rejects(s.get(prefix+"/sample.json",2),/size/);
  await assert.rejects(s.get(prefix+"/../outside",100),/Invalid archive key/);
  await assert.rejects(s.get("/etc/passwd",100),/Invalid archive key/);
  await assert.rejects(s.get(prefix+"/a//b",100),/Invalid archive key/);
  await assert.rejects(s.get(prefix+"/a\\b",100),/Invalid archive key/);
  await write(outside,"secret","private");
  await fs.symlink(path.join(outside,"secret"),path.join(dir,prefix,"link"));
  await fs.symlink(outside,path.join(dir,prefix,"outside"));
  await assert.rejects(s.get(prefix+"/link",100),/symlink/);
  await assert.rejects(s.get(prefix+"/outside/secret",100),/symlink/);
  await assert.rejects(s.get(prefix+"/outside",100),/symlink/);
  await assert.rejects(s.list(prefix+"/",{maxKeys:100}),/symlink/);
  s.close();
});
test("S3-style partial prefix, delimiters, recursive lists and truncation",async t=>{
  const dir=await temp(t),s=local.createLocalStorage(dir),p=prefix+"/historical_st/v2/range=2020-2025/universe=";
  await write(dir,p+"aaa/merged/manifest.json","{}");
  await write(dir,p+"bbb/baostock/manifest.json","{}");
  await fs.mkdir(path.join(dir,p+"empty"),{recursive:true});
  assert.deepEqual(await s.list(p,{delimiter:"/",maxKeys:100}),{keys:[],prefixes:[p+"aaa/",p+"bbb/"],truncated:false});
  assert.deepEqual(await s.list(p,{maxKeys:100}),{keys:[p+"aaa/merged/manifest.json",p+"bbb/baostock/manifest.json"],prefixes:[],truncated:false});
  assert.equal((await s.list(p,{delimiter:"/",maxKeys:1})).truncated,true);
  assert.deepEqual(await s.list(prefix+"/not-published/",{maxKeys:100}),{keys:[],prefixes:[],truncated:false});
  await assert.rejects(s.list(prefix+"/../",{maxKeys:100}),/Invalid archive key/);
  await assert.rejects(s.list(p,{maxKeys:1001}),/listing options/);
});
test("cache scopes separate driver and root; no secrets or paths in key",t=>{
  env(t,{ARCHIVE_STORAGE_DRIVER:"local",ARCHIVE_LOCAL_ROOT:"/data/archive"});
  const a=storage.archiveCacheScope();
  process.env.ARCHIVE_LOCAL_ROOT="/other/archive";
  const b=storage.archiveCacheScope();
  process.env.ARCHIVE_STORAGE_DRIVER="neon";
  const c=storage.archiveCacheScope();
  assert.notEqual(a,b);assert.notEqual(b,c);assert.doesNotMatch(a,/\/data\/archive/);
});
test("real Parquet and financial fixtures: local/Neon transport parity and integrity",{skip:process.env.ARCHIVE_TEST_FIXTURES!=="1"},async t=>{
  const dir=await temp(t);
  execFileSync("python",[path.join(__dirname,"fixtures","generate-archive.py"),dir],{stdio:"inherit"});
  const backing=local.createLocalStorage(dir);
  const sdk=require("@aws-sdk/client-s3");
  let requests=0,parquetReads=0,parquetBytes=0;
  class MockS3{
    constructor(config){assert.equal(config.forcePathStyle,true);}
    async send(cmd){
      requests++;
      if(cmd instanceof sdk.GetObjectCommand){
        const bytes=await backing.get(cmd.input.Key,32*1024*1024);
        if(bytes===null)throw Object.assign(Error("missing"),{name:"NoSuchKey"});
        if(cmd.input.Key.endsWith(".parquet")){parquetReads++;parquetBytes+=bytes.length;}
        return {ContentLength:bytes.length,Body:Readable.from([Buffer.from(bytes)])};
      }
      if(cmd instanceof sdk.ListObjectsV2Command){
        const list=await backing.list(cmd.input.Prefix,{delimiter:cmd.input.Delimiter,maxKeys:cmd.input.MaxKeys});
        return {Contents:list.keys.map(Key=>({Key})),CommonPrefixes:list.prefixes.map(Prefix=>({Prefix})),IsTruncated:list.truncated};
      }
      throw Error("unexpected S3 command");
    }
    destroy(){}
  }
  const modules=loader({"next/cache":cache,"@aws-sdk/client-s3":{...sdk,S3Client:MockS3},hyparquet:await import("hyparquet"),"hyparquet-compressors":await import("hyparquet-compressors")});
  const q=modules("archive-quotes"),r=modules("archive-research");
  env(t,{ARCHIVE_STORAGE_DRIVER:"local",ARCHIVE_LOCAL_ROOT:dir,NEON_STORAGE_ENDPOINT:"https://br-fixture.storage.c-1.us-east-2.aws.neon.tech",NEON_STORAGE_BUCKET:"fixture-private",NEON_STORAGE_READ_ACCESS_KEY_ID:"fixture-only",NEON_STORAGE_READ_SECRET_ACCESS_KEY:"fixture-only",NEON_STORAGE_PRIVATE_CONFIRMED:"true",NEON_STORAGE_REGION:undefined});
  const code="000001.SZ";
  async function results(){
    const annual=await q.archivedQuotes(code,2025),factors=await q.archivedFactors(code,2025);
    const current=await q.incrementalQuotes(code,2026),currentFactors=await q.incrementalFactors(code,2026);
    const recent=await q.recentQuotes(code);
    const recentFactors=await q.recentFactors(code,recent.coverage.from,recent.coverage.to);
    return {annual,factors,current,currentFactors,recent,recentFactors,finance:await r.archivedFinancialHistory(code),overview:await r.archiveOverview()};
  }
  const expected=await results();
  assert.equal(requests,0,"local mode cannot call Neon");
  assert.ok(expected.annual.length>240);
  assert.equal(expected.annual[0].close,11);
  assert.equal(expected.factors[0].factor,2);
  assert.ok(expected.current.bars.length>0);
  assert.equal(expected.currentFactors.factors[0].factor,2);
  assert.equal(expected.recent.bars.length,120);
  assert.equal(expected.recent.coverage.archivedSessions,120);
  assert.equal(expected.recentFactors.length,120);
  assert.equal(expected.finance.points[0].ncav,20);
  assert.equal(expected.finance.points[0].operatingCash,12);
  assert.ok(expected.finance.sources.every(s=>s.state==="ready"));
  assert.equal(expected.overview.find(x=>x.id==="historical_st").years[0].unknown,1);
  assert.match(expected.overview.find(x=>x.id==="income").detail,/1/);
  process.env.ARCHIVE_STORAGE_DRIVER="neon";
  assert.deepEqual(await results(),expected);
  assert.ok(requests>0);
  assert.ok(cacheKeys.some(x=>x.keys[1]?.startsWith("local:")));
  assert.ok(cacheKeys.some(x=>x.keys[1]?.startsWith("neon:")));
  // Compare real Parquet file/byte counts on the same uncached fixture transport.
  const perf=async(fn)=>{
    const reads=parquetReads,bytes=parquetBytes,start=performance.now();
    const value=await fn();
    return {value,reads:parquetReads-reads,bytes:parquetBytes-bytes,ms:performance.now()-start};
  };
  const annualPerf=await perf(()=>q.archivedQuotes(code,2025));
  const monthlyPerf=await perf(()=>q.rangeQuotes(code,"2025-02-05","2025-02-20"));
  assert.equal(annualPerf.reads,12);assert.equal(monthlyPerf.reads,1);
  assert.ok(monthlyPerf.bytes<annualPerf.bytes);
  assert.deepEqual(monthlyPerf.value.bars,annualPerf.value.filter(b=>b.day>="2025-02-05"&&b.day<="2025-02-20"));
  const factorPerf=await perf(()=>q.rangeFactors(code,"2025-02-05","2025-02-20"));
  assert.equal(factorPerf.reads,1);
  assert.deepEqual(factorPerf.value,expected.factors.filter(f=>f.day>="2025-02-05"&&f.day<="2025-02-20"));
  const cross=await perf(()=>q.rangeQuotes(code,"2025-12-29","2026-01-05"));
  assert.equal(cross.reads,2);
  assert.deepEqual(cross.value.bars,[...expected.annual,...expected.current.bars].filter(b=>b.day>="2025-12-29"&&b.day<="2026-01-05"));
  assert.equal(cross.value.coverage.partial,false);
  const crossFactors=await q.rangeFactors(code,"2025-12-29","2026-01-05");
  assert.deepEqual(crossFactors,[...expected.factors,...expected.currentFactors.factors].filter(f=>f.day>="2025-12-29"&&f.day<="2026-01-05"));
  assert.deepEqual((await q.rangeQuotes(code,"2025-02-08","2025-02-09")).bars,[]);
  assert.throws(()=>q.boundedHistoryMonths("2025-01-01","2025-04-01"),/最多三个月/);
  assert.throws(()=>q.boundedHistoryMonths("2025-02-30","2025-03-01"));
  assert.ok(cacheKeys.some(x=>x.keys[0]==="market-quote-month-v1"&&x.opts.revalidate===86400));
  assert.ok(cacheKeys.some(x=>x.keys[0]==="market-incremental-quote-month-v1"&&x.opts.revalidate===60));
  console.log("Monthly range fixture performance:",JSON.stringify({annual:{reads:annualPerf.reads,bytes:annualPerf.bytes,ms:annualPerf.ms},monthly:{reads:monthlyPerf.reads,bytes:monthlyPerf.bytes,ms:monthlyPerf.ms},crossYear:{reads:cross.reads,bytes:cross.bytes,ms:cross.ms}}));
  process.env.ARCHIVE_STORAGE_DRIVER="local";
  assert.deepEqual(await q.rangeQuotes(code,"2025-12-29","2026-01-05"),cross.value);
  assert.deepEqual(await q.rangeFactors(code,"2025-12-29","2026-01-05"),crossFactors);
  const manifest=prefix+"/daily_prices/year=2025/manifest.json";
  const raw=await backing.get(manifest,128*1024);
  const doc=JSON.parse(Buffer.from(raw));
  const file=prefix+"/"+doc.partition_files[0].path;
  const bytes=Buffer.from(await backing.get(file,8*1024*1024));
  const bad=Buffer.from(bytes);bad[0]^=1;
  await write(dir,file,bad);
  await assert.rejects(q.archivedQuotes(code,2025),/Archive checksum mismatch/);
  await assert.rejects(q.rangeQuotes(code,"2025-01-01","2025-01-31"),/Archive checksum mismatch/);
  assert.deepEqual((await q.rangeQuotes(code,"2025-02-05","2025-02-20")).bars,monthlyPerf.value.bars);
  await write(dir,file,bytes);
  doc.partition_files[0].rows++;
  await write(dir,manifest,JSON.stringify(doc));
  await assert.rejects(q.archivedQuotes(code,2025),/Archive row count mismatch/);
  await write(dir,manifest,raw);
  await fs.unlink(path.join(dir,file));
  await assert.rejects(q.archivedQuotes(code,2025),/Missing or empty/);
  await write(dir,file,bytes);
  const financial=prefix+"/financial/income/start=00000_count=050";
  const fm=JSON.parse(Buffer.from(await backing.get(financial+".manifest.json",256*1024)));
  const bomb=gzipSync(Buffer.alloc(33*1024*1024,65));
  fm.compressed_bytes=bomb.length;fm.sha256=createHash("sha256").update(bomb).digest("hex");
  await write(dir,financial+".jsonl.gz",bomb);
  await write(dir,financial+".manifest.json",JSON.stringify(fm));
  assert.equal((await r.archivedFinancialHistory(code)).sources.find(s=>s.kind==="income").state,"error");
  await write(dir,prefix+"/historical_st/v2/range=2020-2025/universe="+"c".repeat(64)+"/merged/manifest.json","{}");
  assert.equal((await r.archiveOverview()).find(x=>x.id==="historical_st").state,"error");
});
