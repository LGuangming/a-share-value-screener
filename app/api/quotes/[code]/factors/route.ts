import { RAW_PREFIX } from "@/lib/archive-paths";
import {NextResponse} from "next/server";
import {archivedFactors} from "@/lib/archive-quotes";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const maxDuration=60;
export async function GET(request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params,year=new URL(request.url).searchParams.get("year")??"";
  const headers={"Cache-Control":"private, no-store"};
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!/^202[0-5]$/.test(year))return NextResponse.json({error:"无效证券或年份"},{status:400,headers});
  try{return NextResponse.json({code,year:Number(year),archive_prefix:RAW_PREFIX,factors:await archivedFactors(code,Number(year))},{headers});}
  catch{return NextResponse.json({error:"该年度复权因子暂不可用或未完整发布"},{status:503,headers});}
}