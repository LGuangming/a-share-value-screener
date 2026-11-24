import "server-only";
import {createHash} from "node:crypto";
import {gunzipSync} from "node:zlib";
import {GetObjectCommand,ListObjectsV2Command} from "@aws-sdk/client-s3";
import {unstable_cache} from "next/cache";
import {storage} from "./archive-quotes";
import {financialHistory,financialKinds,type FinancialKind,type FinancialRecord} from "./research-analysis";

const root="a-share-backtest/v1",phase=root+"/phase2",fin=phase+"/financial_snapshot";
type Store=ReturnType<typeof storage>;
async function get(s:Store,key:string,max:number):Promise<Uint8Array|null>{
  try{
    const r=await s.client.send(new GetObjectCommand({Bucket:s.bucket,Key:key}),{abortSignal:AbortSignal.timeout(12000)});
    if(!r.Body||r.ContentLength===undefined||r.ContentLength<0||r.ContentLength>max)throw Error("Invalid archive size");
    const bytes=await r.Body.transformToByteArray();
    if(bytes.length!==r.ContentLength)throw Error("Truncated archive");
    return bytes;
  }catch(e){const x=e as {name?:string;$metadata?:{httpStatusCode?:number}};if(x.name==="NoSuchKey"||x.$metadata?.httpStatusCode===404)return null;throw e;}
}
async function json(s:Store,key:string,max=256*1024){
  const raw=await get(s,key,max);
  return raw?JSON.parse(new TextDecoder().decode(raw)):null;
}
const int=(v:unknown):v is number=>typeof v==="number"&&Number.isSafeInteger(v)&&v>=0;
async function readFinancial(code:string){
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code))throw Error("Invalid code");
  const s=storage();
  try{
    const universe=await json(s,phase+"/universe/stock_basic_snapshot.json",4*1024*1024);
    if(!universe||universe.status!=="current_snapshot_not_historical"||!Array.isArray(universe.records))throw Error("Missing archive universe");
    const codes=[...new Set<string>(universe.records.map((r:{ts_code:string})=>r.ts_code))].sort();
    const index=codes.indexOf(code);
    if(index<0)return {points:[],sources:financialKinds.map(kind=>({kind,state:"unpublished" as const}))};
    const start=Math.floor(index/50)*50,selected=codes.slice(start,start+50);
    // Python json.dumps defaults to a comma followed by a space.
    const digest=createHash("sha256").update("["+selected.map(x=>JSON.stringify(x)).join(", ")+"]").digest("hex");
    const records:Partial<Record<FinancialKind,FinancialRecord[]>>={};
    const sources=await Promise.all(financialKinds.map(async kind=>{
      try{
        const path=fin+"/"+kind+"/start="+String(start).padStart(5,"0")+"_count=050";
        const m=await json(s,path+".manifest.json");
        if(!m)return {kind,state:"unpublished" as const};
        if(m.format_version!==1||m.kind!==kind||m.start_index!==start||m.requested_codes!==selected.length||m.codes_sha256!==digest||m.object_key!==path+".jsonl.gz"||m.status!=="research_not_pit_certified"||!int(m.rows)||!int(m.compressed_bytes)||m.compressed_bytes>8*1024*1024||!/^[a-f0-9]{64}$/.test(m.sha256))throw Error("Invalid financial manifest");
        const data=await get(s,m.object_key,m.compressed_bytes);
        if(!data||data.length!==m.compressed_bytes||createHash("sha256").update(data).digest("hex")!==m.sha256)throw Error("Financial checksum mismatch");
        const lines=gunzipSync(data,{maxOutputLength:32*1024*1024}).toString("utf8").split("\n").filter(Boolean);
        if(lines.length!==m.rows)throw Error("Financial row count mismatch");
        records[kind]=lines.map(line=>JSON.parse(line) as FinancialRecord).filter(r=>r.ts_code===code);
        return {kind,state:"ready" as const,rows:records[kind]!.length};
      }catch{return {kind,state:"error" as const};}
    }));
    const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
    return {points:financialHistory(records,today),sources};
  }finally{s.client.destroy();}
}
export const archivedFinancialHistory=unstable_cache(readFinancial,["research-financial-v1"],{revalidate:3600});

export type ArchiveDataset={id:string;name:string;years:{year:number;rows:number;sessions:number|null;unknown?:number}[];state:"ready"|"partial"|"unpublished"|"error";detail:string};
async function readArchiveOverview():Promise<ArchiveDataset[]>{
  const s=storage();
  const specs=[{id:"prices",name:"历史日线行情"},{id:"adj_factor",name:"复权因子"},{id:"daily_basic",name:"历史估值"},{id:"stk_limit",name:"涨跌停"},{id:"suspend_d",name:"停复牌"}];
  try{
    const output:ArchiveDataset[]=[];
    for(const spec of specs){
      const checked=await Promise.all(Array.from({length:6},(_,i)=>2020+i).map(async year=>{
        try{
          const m=await json(s,(spec.id==="prices"?root+"/prices":phase+"/"+spec.id)+"/year="+year+"/manifest.json");
          if(!m)return {state:"unpublished" as const};
          const parts=spec.id==="prices"?m.partition_files:m.months;
          if(m.year!==year||m.dataset!==(spec.id==="prices"?"a_share_daily":spec.id)||!Array.isArray(parts)||parts.length!==12||!parts.every((p:{rows:unknown})=>int(p.rows)))throw Error("Invalid manifest");
          const rows=parts.reduce((sum:number,p:{rows:number})=>sum+p.rows,0);
          if(int(m.rows)&&m.rows!==rows)throw Error("Invalid rows");
          const sessions=int(m.sessions)?m.sessions:int(m.trading_days)?m.trading_days:null;
          return {state:"ready" as const,value:{year,rows,sessions}};
        }catch{return {state:"error" as const};}
      }));
      const years=checked.flatMap(c=>c.value?[c.value]:[]);
      output.push({...spec,years,state:years.length===6?"ready":years.length?"partial":checked.some(c=>c.state==="error")?"error":"unpublished",detail:"已发布年度清单 "+years.length+"/6"});
    }

    // Locate the frozen 2020–2025 ST namespace without guessing the universe hash.
    const stPrefix=phase+"/historical_st/v2/range=2020-2025/universe=";
    try{
      const list=await s.client.send(new ListObjectsV2Command({Bucket:s.bucket,Prefix:stPrefix,Delimiter:"/",MaxKeys:100}),{abortSignal:AbortSignal.timeout(8000)});
      const namespaces=(list.CommonPrefixes??[]).map(p=>p.Prefix??"").filter(p=>p.startsWith(stPrefix)&&/^[a-f0-9]{64}\/$/.test(p.slice(stPrefix.length)));
      if(list.IsTruncated||namespaces.length>1)throw Error("Ambiguous ST namespace");
      if(!namespaces.length)output.push({id:"historical_st",name:"历史ST",years:[],state:"unpublished",detail:"暂无已发布清单"});
      else{
        const base=namespaces[0],hash=base.slice(stPrefix.length,-1);
        let source="merged",m=await json(s,base+source+"/manifest.json",1024*1024);
        if(!m){source="baostock";m=await json(s,base+source+"/manifest.json",1024*1024);}
        if(!m)output.push({id:"historical_st",name:"历史ST",years:[],state:"unpublished",detail:"暂无已发布清单"});
        else{
          if(m.source!==source||m.universe_sha256!==hash||!Array.isArray(m.year_manifests)||!int(m.rows)||m.rows<=0)throw Error("Invalid ST manifest");
          const years:{year:number;rows:number;sessions:null;unknown:number}[]=m.year_manifests.map((y:{year:number;rows:number;resolution_counts:Record<string,number>})=>{
            const unknown=y.resolution_counts?.unknown??0;
            if(!int(y.year)||y.year<2020||y.year>2025||!int(y.rows)||!int(unknown)||unknown>y.rows||!y.resolution_counts||!Object.values(y.resolution_counts).every(int)||Object.values(y.resolution_counts).reduce((a,b)=>a+b,0)!==y.rows)throw Error("Invalid ST coverage");
            return {year:y.year,rows:y.rows,sessions:null,unknown};
          });
          if(new Set(years.map(y=>y.year)).size!==years.length||years.reduce((a,y)=>a+y.rows,0)!==m.rows)throw Error("Invalid ST totals");
          const unknown=years.reduce((a,y)=>a+y.unknown,0);
          output.push({id:"historical_st",name:"历史ST",years,state:years.length===6&&unknown===0?"ready":"partial",detail:(source==="merged"?"合并源":"BaoStock")+" · 已知 "+((m.rows-unknown)/m.rows*100).toFixed(2)+"% · 未知 "+unknown.toLocaleString("zh-CN")+" 条"});
        }
      }
    }catch{output.push({id:"historical_st",name:"历史ST",years:[],state:"error",detail:"历史ST清单暂不可读"});}
    // Listing counts are publication counts only; not effective security coverage.
    for(const kind of financialKinds){
      try{
        const r=await s.client.send(new ListObjectsV2Command({Bucket:s.bucket,Prefix:fin+"/"+kind+"/",MaxKeys:1000}),{abortSignal:AbortSignal.timeout(12000)});
        if(r.IsTruncated)throw Error("Incomplete manifest list");
        const manifests=(r.Contents??[]).filter(x=>/\/start=\d{5}_count=050\.manifest\.json$/.test(x.Key??"")).length;
        output.push({id:kind,name:({balancesheet:"历史资产负债表",income:"历史利润表",cashflow:"历史现金流量表",fina_indicator:"历史财务指标"})[kind],years:[],state:manifests?"partial":"unpublished",detail:"已发布批次 "+manifests+"（每批最多50只）"});
      }catch{output.push({id:kind,name:kind,years:[],state:"error",detail:"批次清单暂不可读"});}
    }
    return output;
  }finally{s.client.destroy();}
}
export const archiveOverview=unstable_cache(readArchiveOverview,["research-archive-overview-v1"],{revalidate:600});
