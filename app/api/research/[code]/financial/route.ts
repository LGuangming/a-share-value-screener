import {NextResponse} from "next/server";
import {archivedFinancialHistory} from "@/lib/archive-research";
export const dynamic="force-dynamic";
export const runtime="nodejs";
export const maxDuration=60;
export async function GET(_request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params;
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code))return NextResponse.json({error:"无效证券代码"},{status:400});
  try{return NextResponse.json({code,...await archivedFinancialHistory(code)});}
  catch{return NextResponse.json({error:"历史财务档案暂不可用"},{status:503});}
}
