// Authenticated readiness, no public health bypass. No credentials on stdout.
const assert=require("node:assert/strict");
async function main(){
  const u=new URL(process.env.ASHARE_READINESS_URL||"");
  assert(u.protocol==="https:"||(u.protocol==="http:"&&u.hostname==="127.0.0.1"));
  assert(!u.username&&!u.password&&u.pathname==="/"&&!u.search&&!u.hash);
  const base=u.origin,password=process.env.ASHARE_READINESS_PASSWORD;
  const code=process.env.ASHARE_READINESS_STOCK,query=process.env.ASHARE_READINESS_QUERY,year=process.env.ASHARE_READINESS_YEAR;
  assert(password&&/^[0-9]{6}\.(SZ|SH|BJ)$/.test(code||"")&&query&&/^20[0-9]{2}$/.test(year||""));
  const req=(path,opts={})=>fetch(base+path,{redirect:"manual",signal:AbortSignal.timeout(20000),...opts});
  assert.equal((await req("/api/health")).status,401);
  assert.equal((await req("/login")).status,200);
  const login=await req("/login",{method:"POST",headers:{"content-type":"application/x-www-form-urlencoded",origin:base},body:new URLSearchParams({password,next:"/methodology"})});
  assert.equal(login.status,303);
  const cookie=login.headers.get("set-cookie")||"";
  assert(/^__Host-value-radar-access=/.test(cookie)&&/;\s*Secure/i.test(cookie)&&/;\s*HttpOnly/i.test(cookie)&&/;\s*SameSite=Lax/i.test(cookie)&&/;\s*Path=\//i.test(cookie));
  assert(!/;\s*Domain=/i.test(cookie));
  assert.equal(login.headers.get("location"),"/methodology");
  const destination=new URL(login.headers.get("location"),base);
  assert.equal(destination.pathname,"/methodology");
  assert.equal(destination.origin,base);
  const headers={cookie:cookie.split(";")[0]};
  const search=await req("/api/research/search?q="+encodeURIComponent(query),{headers});
  assert.equal(search.status,200);assert((await search.json()).stocks.some(x=>x.ts_code===code));
  const bars=await req("/api/quotes/"+code+"?year="+year,{headers});
  assert.equal(bars.status,200);assert((await bars.json()).bars.length>0);
  const factors=await req("/api/quotes/"+code+"/factors?year="+year,{headers});
  assert.equal(factors.status,200);assert((await factors.json()).factors.length>0);
  const health=await req("/api/health",{headers});
  if(process.env.ASHARE_TEST_ALLOW_STALE==="1"){
    assert.equal(u.hostname,"127.0.0.1");assert([200,503].includes(health.status));
    console.log("PASS: fixture SQL/archive checks; snapshot freshness not required");
  }else{
    assert.equal(health.status,200);assert.equal((await health.json()).status,"ok");
    console.log("PASS: authenticated SQL/archive/snapshot readiness");
  }
}
main().catch(()=>{console.error("FAIL: authenticated readiness check (configuration, login, SQL, archive or snapshot)");process.exitCode=1;});
