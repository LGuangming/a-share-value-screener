import { RAW_PREFIX } from "@/lib/archive-paths";
import { NextResponse } from "next/server";
import { archivedQuotes, incrementalQuotes, recentQuotes, rangeQuotes, boundedHistoryMonths, ArchiveUnavailable } from "@/lib/archive-quotes";

export const runtime="nodejs";
export const dynamic="force-dynamic";
export const maxDuration=60;
export async function GET(request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params;
  const query=new URL(request.url).searchParams;
  const yearText=query.get("year")??"";
  const recent=query.get("range")==="recent",custom=query.get("range")==="custom";
  const from=query.get("from")??"",to=query.get("to")??"";
  const year=Number(yearText);
  const headers={"Cache-Control":"private, no-store"};
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||(!recent&&!custom&&(!/^20\d{2}$/.test(yearText)||year<2020||year>new Date(Date.now()+8*3600000).getUTCFullYear())))
    return NextResponse.json({error:"无效股票代码或年份"},{status:400,headers});
  if(custom){try{boundedHistoryMonths(from,to);}catch{return NextResponse.json({error:"无效行情期间（每次最多三个月）"},{status:400,headers});}}
  try{
    if(custom){const {bars,coverage}=await rangeQuotes(code,from,to);return NextResponse.json({code,range:"custom",from,to,adjustment:"unadjusted",archive_prefix:RAW_PREFIX,bars,coverage},{headers});}
    if(recent){const {bars,coverage}=await recentQuotes(code);return NextResponse.json({code,range:"recent",adjustment:"unadjusted",archive_prefix:RAW_PREFIX,bars,coverage},{headers});}
    if(year>=2026){const {bars,coverage}=await incrementalQuotes(code,year);return NextResponse.json({code,year,adjustment:"unadjusted",archive_prefix:RAW_PREFIX,bars,coverage},{headers});}
    const bars=await archivedQuotes(code,year);
    return NextResponse.json({code,year,adjustment:"unadjusted",archive_prefix:RAW_PREFIX,bars},{headers});
  }catch(e){
    if(e instanceof ArchiveUnavailable)return NextResponse.json({error:e.message},{status:503,headers});
    console.error("Historical quote archive query failed");
    return NextResponse.json({error:"历史行情暂不可用，请稍后重试"},{status:503,headers});
  }
}
