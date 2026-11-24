import {NextResponse} from "next/server";
import {searchStockIdentities} from "@/lib/data";
export const dynamic="force-dynamic";
export async function GET(request:Request){
  const q=new URL(request.url).searchParams.get("q")?.trim()??"";
  if(!q||q.length>40)return NextResponse.json({stocks:[]});
  try{return NextResponse.json({stocks:await searchStockIdentities(q)});}
  catch{return NextResponse.json({error:"股票搜索暂不可用"},{status:503});}
}
