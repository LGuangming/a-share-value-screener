import {NextRequest,NextResponse} from "next/server";
import {accessConfig,validSession,COOKIE_NAME,PRIVATE_HEADERS,safeReturnPath} from "@/lib/site-access";

export function proxy(request:NextRequest){
  const path=request.nextUrl.pathname;
  // Only immutable framework assets and the isolated password form are public.
  if(path.startsWith("/_next/static/")||path==="/login")return NextResponse.next();
  const config=accessConfig();
  if(!config)return NextResponse.json({error:"网站访问验证尚未配置"},{status:503,headers:PRIVATE_HEADERS});
  if(validSession(request.cookies.get(COOKIE_NAME)?.value,config)){
    const response=NextResponse.next();
    for(const [key,value] of Object.entries(PRIVATE_HEADERS))response.headers.set(key,value);
    return response;
  }
  if(path.startsWith("/api/")||path==="/api"||path.startsWith("/_next/")||!["GET","HEAD"].includes(request.method))
    return NextResponse.json({error:"需要网站访问密码"},{status:401,headers:PRIVATE_HEADERS});
  const login=request.nextUrl.clone();
  login.pathname="/login";login.search="";
  login.searchParams.set("next",safeReturnPath(path+request.nextUrl.search));
  const response=NextResponse.redirect(login,307);
  for(const [key,value] of Object.entries(PRIVATE_HEADERS))response.headers.set(key,value);
  return response;
}
export const config={matcher:["/:path*"]};
