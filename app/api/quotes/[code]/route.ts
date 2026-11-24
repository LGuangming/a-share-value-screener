import { NextResponse } from "next/server";
import { archivedQuotes, ArchiveUnavailable } from "@/lib/archive-quotes";

export const runtime="nodejs";
export const dynamic="force-dynamic";
export const maxDuration=60;
export async function GET(request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params;
  const yearText=new URL(request.url).searchParams.get("year")??"";
  const year=Number(yearText);
  const headers={"Cache-Control":"private, no-store"};
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!/^202[0-5]$/.test(yearText))
    return NextResponse.json({error:"无效股票代码或年份"},{status:400,headers});
  try{
    const bars=await archivedQuotes(code,year);
    return NextResponse.json({code,year,adjustment:"unadjusted",bars},{headers});
  }catch(e){
    if(e instanceof ArchiveUnavailable)return NextResponse.json({error:e.message},{status:503,headers});
    console.error("Historical quote archive query failed");
    return NextResponse.json({error:"历史行情暂不可用，请稍后重试"},{status:503,headers});
  }
}
