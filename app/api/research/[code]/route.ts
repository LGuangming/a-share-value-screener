import {NextResponse} from "next/server";
import {stockDetail,companyOverview} from "@/lib/data";
export const dynamic="force-dynamic";
export async function GET(_request:Request,{params}:{params:Promise<{code:string}>}){
  const {code}=await params;
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code))return NextResponse.json({error:"无效股票代码"},{status:400});
  try{
    const [company,detail]=await Promise.all([companyOverview(code),stockDetail(code)]);
    if(!company)return NextResponse.json({error:"未找到该证券"},{status:404});
    return NextResponse.json({...company,...detail});
  }catch{return NextResponse.json({error:"研究数据暂不可用"},{status:503});}
}
