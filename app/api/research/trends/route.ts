import {NextResponse} from "next/server";
import {recentMiniTrends} from "@/lib/data";
export const dynamic="force-dynamic";
export async function GET(request:Request){
  const raw=new URL(request.url).searchParams.get("codes")??"";
  const codes=raw.split(",").filter(Boolean);
  if(!codes.length||codes.length>25||codes.some(c=>!/^\d{6}\.(SH|SZ|BJ)$/.test(c)))return NextResponse.json({error:"无效证券列表"},{status:400});
  try{return NextResponse.json({trends:await recentMiniTrends(codes)});}
  catch{return NextResponse.json({error:"近期走势暂不可用"},{status:503});}
}
