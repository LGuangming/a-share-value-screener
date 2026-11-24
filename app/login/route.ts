import {NextRequest,NextResponse} from "next/server";
import {accessConfig,validSession,passwordMatches,issueSession,sessionCookie,COOKIE_NAME,PRIVATE_HEADERS,safeReturnPath,sameOriginPost} from "@/lib/site-access";
export const dynamic="force-dynamic";
export const runtime="nodejs";

const headers={...PRIVATE_HEADERS,"Content-Type":"text/html; charset=utf-8","Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'","X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY"};
const escape=(s:string)=>s.replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
function render(next:string,error="",status=200,configured=true){
  const html=`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>访问验证 | 价值雷达</title><style>
  *{box-sizing:border-box}body{margin:0;min-height:100vh;min-height:100dvh;display:grid;place-items:center;background:#f5f6f8;color:#242a30;font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;font-size:14px;padding:24px}.gate{width:min(100%,390px);background:white;border:1px solid #dfe2e5;border-radius:6px;overflow:hidden;box-shadow:0 4px 24px #1a202910}.brand{background:#252a31;color:white;padding:22px 28px;border-top:4px solid #d04a02}.brand strong{display:block;font-size:22px;letter-spacing:.04em}.brand small{display:block;color:#b5bbc2;font-size:11px;margin-top:4px}.body{padding:26px 28px}h1{font-size:18px;margin:0 0 8px}p{font-size:13px;line-height:1.6;color:#626a73;margin:0 0 20px}label{display:block;font-size:12px;font-weight:600;margin-bottom:7px}input{width:100%;height:42px;border:1px solid #cdd1d5;border-radius:4px;padding:10px 12px;font:inherit;color:#242a30}input:focus{outline:2px solid #d04a02;outline-offset:1px}button{width:100%;margin-top:14px;border:0;border-radius:4px;background:#d04a02;color:white;font:inherit;font-weight:600;height:42px;cursor:pointer}button:hover{background:#b74102}.error{font-size:12px;color:#a12a30;margin:9px 0 0}.foot{border-top:1px solid #e9ebed;padding:12px 28px;color:#737b83;font-size:11px}
  </style></head><body><main class="gate"><header class="brand"><strong>价值雷达</strong><small>A 股多策略研究</small></header><div class="body"><h1>网站访问验证</h1><p>${configured?"请输入访问密码。":"网站访问验证尚未配置，请联系网站管理员。"}</p>${configured?`<form method="post" action="/login"><input type="hidden" name="next" value="${escape(next)}"><label for="password">访问密码</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512" autofocus placeholder="请输入访问密码">${error?`<p class="error" role="alert">${escape(error)}</p>`:""}<button type="submit">进入网站</button></form>`:""}</div><footer class="foot">A-share Multi-Strategy Research · 私有研究工作台</footer></main></body></html>`;
  return new NextResponse(html,{status,headers});
}
function redirect(request:NextRequest,next:string){
  const url=request.nextUrl.clone(),target=new URL(next,"https://internal.invalid");
  url.pathname=target.pathname;url.search=target.search;
  return NextResponse.redirect(url,{status:303,headers:PRIVATE_HEADERS});
}
export async function GET(request:NextRequest){
  const next=safeReturnPath(request.nextUrl.searchParams.get("next")),config=accessConfig();
  if(!config)return render("/", "",503,false);
  if(validSession(request.cookies.get(COOKIE_NAME)?.value,config))return redirect(request,next);
  return render(next);
}
async function boundedForm(request:Request){
  if(!request.headers.get("content-type")?.toLowerCase().startsWith("application/x-www-form-urlencoded")||!request.body)throw Error("invalid form");
  const length=Number(request.headers.get("content-length")??0);
  if(!Number.isFinite(length)||length<0||length>8192)throw Error("oversize");
  const reader=request.body.getReader(),decoder=new TextDecoder("utf-8",{fatal:true});
  let total=0,text="";
  try{
    while(true){
      const {value,done}=await reader.read();if(done)break;
      total+=value.byteLength;
      if(total>8192){await reader.cancel();throw Error("oversize");}
      text+=decoder.decode(value,{stream:true});
    }
    text+=decoder.decode();
    return new URLSearchParams(text);
  }finally{reader.releaseLock();}
}
export async function POST(request:NextRequest){
  const config=accessConfig();
  if(!config)return render("/", "",503,false);
  if(!sameOriginPost(request))return render("/","请求来源无效。",403);
  let fields:URLSearchParams;
  try{fields=await boundedForm(request);}catch{return render("/","无效请求。",400);}
  const next=safeReturnPath(fields.get("next"));
  if(!passwordMatches(fields.get("password")??"",config)){
    // Small fixed delay is not a distributed rate limit. Configure edge rate limiting separately.
    await new Promise(resolve=>setTimeout(resolve,700));
    return render(next,"访问密码错误，请重试。",401);
  }
  const response=redirect(request,next);
  response.cookies.set(sessionCookie(issueSession(config)));
  return response;
}
