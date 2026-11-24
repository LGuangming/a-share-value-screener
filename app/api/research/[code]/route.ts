import {NextResponse} from "next/server";
import {stockDetail,searchStockIdentities} from "@/lib/data";
export const dynamic="force-dynamic";
export async function GET(_request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params;
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code))return NextResponse.json({error:"无效股票代码"},{status:400});
  try{
    const [identities,detail]=await Promise.all([searchStockIdentities(code),stockDetail(code)]);
    const identity=identities.find(x=>x.ts_code===code);
    if(!identity)return NextResponse.json({error:"未找到该证券"},{status:404});
    return NextResponse.json({identity,...detail});
  }catch{return NextResponse.json({error:"研究数据暂不可用"},{status:503});}
}
