// Run against the actual production build with throwaway credentials only.
const assert=require("node:assert/strict");
const {spawn}=require("node:child_process");
const {randomBytes}=require("node:crypto");
const port=3199,base="http://127.0.0.1:"+port;
const password=randomBytes(3).toString("hex"),secret=randomBytes(32).toString("hex");
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function server(configured,run){
  const env={...process.env,NODE_ENV:"production"};
  delete env.SITE_ACCESS_PASSWORD;delete env.SITE_SESSION_SECRET;
  if(configured){env.SITE_ACCESS_PASSWORD=password;env.SITE_SESSION_SECRET=secret;}
  const child=spawn(process.execPath,["node_modules/next/dist/bin/next","start","--hostname","127.0.0.1","--port",String(port)],{env,stdio:["ignore","inherit","inherit"]});
  try{
    let ready=false;
    for(let i=0;i<80;i++){
      if(child.exitCode!==null)throw Error("Next server exited");
      try{const r=await fetch(base+"/login",{redirect:"manual",signal:AbortSignal.timeout(1500)});if(r.status===200||r.status===503){ready=true;break;}}catch{}
      await sleep(250);
    }
    assert.ok(ready,"Next server is ready");
    await run();
  }finally{
    if(child.exitCode===null){child.kill("SIGTERM");await new Promise(resolve=>{child.once("exit",resolve);setTimeout(()=>{child.kill("SIGKILL");resolve();},3000).unref();});}
  }
}
const req=(path,init={})=>fetch(base+path,{redirect:"manual",...init,signal:AbortSignal.timeout(15000)});
const post=(path,fields,extra={})=>req(path,{method:"POST",headers:{"content-type":"application/x-www-form-urlencoded",origin:base,...extra},body:new URLSearchParams(fields)});
(async()=>{
  await server(false,async()=>{
    assert.equal((await req("/")).status,503);
    assert.equal((await req("/api/research/search")).status,503);
    assert.equal((await req("/login")).status,503);
  });
  await server(true,async()=>{
    const login=await req("/login");assert.equal(login.status,200);
    assert.match(await login.text(),/type="password"/);
    const gate=await req("/research?code=000001.SZ");assert.equal(gate.status,307);
    assert.equal(new URL(gate.headers.get("location"),base).pathname,"/login");
    assert.equal(new URL(gate.headers.get("location"),base).searchParams.get("next"),"/research?code=000001.SZ");
    for(const path of ["/api/research/search","/api/quotes/000001.SZ?year=2025","/api/research/000001.SZ/financial"])assert.equal((await req(path)).status,401);
    assert.equal((await req("/api/research/search",{headers:{cookie:"__Host-value-radar-access=forged"}})).status,401);
    const wrong=await post("/login",{password:"wrong",next:"/methodology"});
    assert.equal(wrong.status,401);assert.equal(wrong.headers.get("set-cookie"),null);
    assert.equal((await post("/login",{password},{origin:"https://external.invalid"})).status,403);
    const correct=await post("/login",{password,next:"/methodology"});
    assert.equal(correct.status,303);
    assert.equal(new URL(correct.headers.get("location"),base).pathname,"/methodology");
    const setCookie=correct.headers.get("set-cookie");assert.ok(setCookie);
    assert.match(setCookie,/HttpOnly/i);assert.match(setCookie,/Secure/i);assert.match(setCookie,/SameSite=Lax/i);
    const cookie=setCookie.split(";")[0];
    const allowed=await req("/api/research/search",{headers:{cookie}});assert.equal(allowed.status,200);
    assert.match(allowed.headers.get("cache-control")||"",/no-store/);
    assert.equal((await req("/methodology",{headers:{cookie}})).status,200);
    const logout=await post("/api/access/logout",{}, {cookie});
    assert.equal(logout.status,303);assert.match(logout.headers.get("set-cookie")||"",/Max-Age=0/i);
    assert.equal((await req("/api/research/search")).status,401);
  });
  console.log("Site access HTTP smoke tests passed");
})().catch(e=>{console.error(e);process.exitCode=1;});
