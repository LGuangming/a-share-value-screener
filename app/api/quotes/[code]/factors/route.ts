import { RAW_PREFIX } from "@/lib/archive-paths";
import {NextResponse} from "next/server";
import {archivedFactors,incrementalFactors,recentFactors,rangeFactors,boundedHistoryMonths} from "@/lib/archive-quotes";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const maxDuration=60;
export async function GET(request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params,query=new URL(request.url).searchParams,year=query.get("year")??"";
  const recent=query.get("range")==="recent",custom=query.get("range")==="custom";
  const from=query.get("from")??"",to=query.get("to")??"";
  const headers={"Cache-Control":"private, no-store"};
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||(!recent&&!custom&&(!/^20\d{2}$/.test(year)||Number(year)<2020||Number(year)>new Date(Date.now()+8*3600000).getUTCFullYear())))return NextResponse.json({error:"无效证券或年份"},{status:400,headers});
  if(custom){try{boundedHistoryMonths(from,to);}catch{return NextResponse.json({error:"无效复权期间（每次最多三个月）"},{status:400,headers});}}
  try{if(custom)return NextResponse.json({code,range:"custom",from,to,archive_prefix:RAW_PREFIX,factors:await rangeFactors(code,from,to)},{headers});if(recent){const from=query.get("from")??"",to=query.get("to")??"";return NextResponse.json({code,range:"recent",from,to,archive_prefix:RAW_PREFIX,factors:await recentFactors(code,from,to)},{headers});}if(Number(year)>=2026){const {factors,coverage}=await incrementalFactors(code,Number(year));return NextResponse.json({code,year:Number(year),archive_prefix:RAW_PREFIX,factors,coverage},{headers});}return NextResponse.json({code,year:Number(year),archive_prefix:RAW_PREFIX,factors:await archivedFactors(code,Number(year))},{headers});}
  catch{return NextResponse.json({error:"该年度复权因子暂不可用或未完整发布"},{status:503,headers});}
}