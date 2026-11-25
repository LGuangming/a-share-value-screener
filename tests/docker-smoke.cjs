// CI-only integration against a real running standalone image.
const assert=require("node:assert/strict");
const base="http://127.0.0.1:3198",password=process.env.DOCKER_FIXTURE_PASSWORD;
assert.ok(password);
const req=(path,opts={})=>fetch(base+path,{redirect:"manual",signal:AbortSignal.timeout(20000),...opts});
(async()=>{
  const login=await req("/login");assert.equal(login.status,200);
  assert.match(await login.text(),/type="password"/);
  const gate=await req("/");assert.equal(gate.status,307);
  for(const p of ["/api/research/search?q=Docker","/api/quotes/000001.SZ?year=2025","/api/health"])
    assert.equal((await req(p)).status,401);
  const attempt=await req("/login",{method:"POST",headers:{"content-type":"application/x-www-form-urlencoded",origin:base},body:new URLSearchParams({password,next:"/methodology"})});
  assert.equal(attempt.status,303);
  const cookie=attempt.headers.get("set-cookie");assert.ok(cookie);assert.match(cookie,/Secure/i);
  const headers={cookie:cookie.split(";")[0]};
  const search=await req("/api/research/search?q=Docker",{headers});assert.equal(search.status,200);
  assert.ok((await search.json()).stocks.some(x=>x.ts_code==="000001.SZ"));
  const quotes=await req("/api/quotes/000001.SZ?year=2025",{headers});assert.equal(quotes.status,200);
  const data=await quotes.json();assert.ok(data.bars.length>240);assert.equal(data.bars[0].close,11);
  const factors=await req("/api/quotes/000001.SZ/factors?year=2025",{headers});assert.equal(factors.status,200);
  assert.ok((await factors.json()).factors.length>240);
  const page=await req("/methodology",{headers});assert.equal(page.status,200);
  const html=await page.text(),asset=html.match(/(?:src|href)="(\/_next\/static\/[^" ]+\.(?:js|css))"/);
  assert.ok(asset,"standalone HTML references a static asset");
  assert.equal((await req(asset[1])).status,200);
  console.log("Container login, SQL reader, Parquet and static asset checks passed");
})().catch(e=>{console.error(e);process.exitCode=1;});
