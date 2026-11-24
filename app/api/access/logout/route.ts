import {NextRequest,NextResponse} from "next/server";
import {PRIVATE_HEADERS,sessionCookie,sameOriginPost} from "@/lib/site-access";
export const dynamic="force-dynamic";
export async function POST(request:NextRequest){
  if(!sameOriginPost(request))return NextResponse.json({error:"请求来源无效"},{status:403,headers:PRIVATE_HEADERS});
  const url=request.nextUrl.clone();url.pathname="/login";url.search="";
  const response=NextResponse.redirect(url,{status:303,headers:PRIVATE_HEADERS});
  response.cookies.set(sessionCookie("",0));
  return response;
}
