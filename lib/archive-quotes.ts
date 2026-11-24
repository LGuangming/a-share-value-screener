import "server-only";
import { createHash } from "node:crypto";
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import { parquetReadObjects } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import { unstable_cache } from "next/cache";
import { completeBar, type MarketBar, type Factor } from "./market-bars";

import { RAW_PREFIX as PREFIX } from "./archive-paths";
export class ArchiveUnavailable extends Error {}
export function storage(){
  const endpoint=process.env.NEON_STORAGE_ENDPOINT;
  const bucket=process.env.NEON_STORAGE_BUCKET;
  const accessKeyId=process.env.NEON_STORAGE_READ_ACCESS_KEY_ID;
  const secretAccessKey=process.env.NEON_STORAGE_READ_SECRET_ACCESS_KEY;
  if(!endpoint||!bucket||!accessKeyId||!secretAccessKey||process.env.NEON_STORAGE_PRIVATE_CONFIRMED!=="true")
    throw new ArchiveUnavailable("历史行情读取尚未配置");
  const url=new URL(endpoint);
  const match=/^br-[a-z0-9-]+\.storage\.c-\d+\.([a-z0-9-]+)\.aws\.neon\.tech$/.exec(url.hostname);
  if(url.protocol!=="https:"||!match||url.port||url.username||url.password||url.search||url.hash||url.pathname!=="/"||!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket))
    throw new ArchiveUnavailable("历史行情读取配置无效");
  return {bucket,client:new S3Client({endpoint,region:process.env.NEON_STORAGE_REGION||match[1],forcePathStyle:true,credentials:{accessKeyId,secretAccessKey},maxAttempts:2})};
}
function isoDay(value:unknown):string {
  if(value instanceof Date)return value.toISOString().slice(0,10);
  if(typeof value==="number"&&Number.isInteger(value)&&value>10000&&value<30000)return new Date(value*86400000).toISOString().slice(0,10);
  if(typeof value==="string"&&/^\d{4}-\d{2}-\d{2}/.test(value))return value.slice(0,10);
  throw new Error("Invalid Parquet date");
}
function number(value:unknown):number {
  if(typeof value!=="number"||!Number.isFinite(value))throw new Error("Invalid Parquet numeric value");
  return value;
}
type Part={path:string;month:string;bytes:number;sha256:string;rows:number};
async function queryYear(code:string,year:number):Promise<MarketBar[]>{
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!Number.isInteger(year)||year<2020||year>2025)throw new Error("Invalid query");
  const {bucket,client}=storage();
  async function get(key:string,max:number){
    const result=await client.send(new GetObjectCommand({Bucket:bucket,Key:key}),{abortSignal:AbortSignal.timeout(15000)});
    if(!result.Body||!result.ContentLength||result.ContentLength>max)throw new Error("Invalid archive object size");
    const bytes=await result.Body.transformToByteArray();
    if(bytes.byteLength!==result.ContentLength)throw new Error("Truncated archive object");
    return bytes;
  }
  try {
    const raw=await get(PREFIX+"/daily_prices/year="+year+"/manifest.json",128*1024);
    const doc=JSON.parse(new TextDecoder().decode(raw));
    if(doc.format_version!==1||doc.dataset!=="a_share_daily"||doc.adjustment!=="unadjusted"||doc.year!==year||!Array.isArray(doc.partition_files)||doc.partition_files.length!==12)throw new Error("Invalid year manifest");
    const parts:Part[]=doc.partition_files;
    const months=new Set<string>();
    for(const p of parts){
      if(!p||typeof p.month!=="string"||!/^(0[1-9]|1[0-2])$/.test(p.month)||months.has(p.month)||p.path!=="daily_prices/year="+year+"/month="+p.month+".parquet"||!Number.isInteger(p.bytes)||p.bytes<=0||p.bytes>8*1024*1024||!/^[a-f0-9]{64}$/.test(p.sha256)||!Number.isInteger(p.rows)||p.rows<=0)throw new Error("Invalid archive partition");
      months.add(p.month);
    }
    const bars:MarketBar[]=[];
    // Bounded batches: preserve the original monthly objects and verify every read.
    for(let i=0;i<parts.length;i+=2){
      const result=await Promise.all(parts.slice(i,i+2).map(async p=>{
        const bytes=await get(PREFIX+"/"+p.path,p.bytes);
        if(bytes.byteLength!==p.bytes||createHash("sha256").update(bytes).digest("hex")!==p.sha256)throw new Error("Archive checksum mismatch");
        const file=new Uint8Array(bytes).buffer;
        const records=await parquetReadObjects({file,compressors,columns:["ts_code","trade_date","open","high","low","close","vol","amount"]});
        if(records.length!==p.rows)throw new Error("Archive row count mismatch");
        return records.filter(r=>r.ts_code===code).map(r=>{
          const b={day:isoDay(r.trade_date),open:number(r.open),high:number(r.high),low:number(r.low),close:number(r.close),volume:number(r.vol),amount:number(r.amount)};
          if(!b.day.startsWith(year+"-"+p.month+"-")||!completeBar(b)||b.volume<0||b.amount<0)throw new Error("Invalid archived bar");
          return b;
        });
      }));
      bars.push(...result.flat());
    }
    bars.sort((a,b)=>a.day.localeCompare(b.day));
    if(new Set(bars.map(b=>b.day)).size!==bars.length)throw new Error("Duplicate archived day");
    return bars;
  } finally {client.destroy();}
}
export const archivedQuotes=unstable_cache(queryYear,["market-archive-raw-v1"],{revalidate:86400});

async function queryFactors(code:string,year:number):Promise<Factor[]>{
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!Number.isInteger(year)||year<2020||year>2025)throw Error("Invalid factor query");
  const {bucket,client}=storage();
  const prefix=PREFIX+"/adj_factor/year="+year;
  async function get(key:string,max:number){
    const r=await client.send(new GetObjectCommand({Bucket:bucket,Key:key}),{abortSignal:AbortSignal.timeout(12000)});
    if(!r.Body||!r.ContentLength||r.ContentLength>max)throw Error("Invalid factor object size");
    const bytes=await r.Body.transformToByteArray();
    if(bytes.length!==r.ContentLength)throw Error("Truncated factor object");
    return bytes;
  }
  try{
    const doc=JSON.parse(new TextDecoder().decode(await get(prefix+"/manifest.json",256*1024)));
    if(doc.format_version!==1||doc.dataset!=="adj_factor"||doc.year!==year||!Array.isArray(doc.months)||doc.months.length!==12)throw Error("Incomplete factor manifest");
    const months=new Set<string>();
    let total=0,sessions=0;
    for(const p of doc.months){
      if(p.format_version!==1||p.dataset!=="adj_factor"||p.year!==year||typeof p.month!=="string"||!/^(0[1-9]|1[0-2])$/.test(p.month)||months.has(p.month)||p.object_key!==prefix+"/month="+p.month+".parquet"||!Array.isArray(p.fields)||p.fields.join(",")!=="ts_code,trade_date,adj_factor"||!Number.isSafeInteger(p.parquet_bytes)||p.parquet_bytes<=0||p.parquet_bytes>8*1024*1024||!Number.isSafeInteger(p.rows)||p.rows<=0||!Number.isSafeInteger(p.sessions)||p.sessions<=0||!/^[a-f0-9]{64}$/.test(p.sha256))throw Error("Invalid factor partition");
      months.add(p.month);total+=p.rows;sessions+=p.sessions;
    }
    if(doc.rows!==total||doc.sessions!==sessions)throw Error("Factor manifest totals mismatch");
    const factors:Factor[]=[];
    for(let i=0;i<doc.months.length;i+=2){
      const parts=await Promise.all(doc.months.slice(i,i+2).map(async (p:{object_key:string;parquet_bytes:number;rows:number;month:string;sha256:string})=>{
        const bytes=await get(p.object_key,p.parquet_bytes);
        if(bytes.length!==p.parquet_bytes||createHash("sha256").update(bytes).digest("hex")!==p.sha256)throw Error("Factor checksum mismatch");
        const rows=await parquetReadObjects({file:new Uint8Array(bytes).buffer,compressors,columns:["ts_code","trade_date","adj_factor"]});
        if(rows.length!==p.rows)throw Error("Factor row count mismatch");
        return rows.filter(r=>r.ts_code===code).map(r=>{
          const day=isoDay(r.trade_date),factor=number(r.adj_factor);
          if(!day.startsWith(year+"-"+p.month+"-")||factor<=0)throw Error("Invalid adjustment factor");
          return {day,factor};
        });
      }));
      factors.push(...parts.flat());
    }
    factors.sort((a,b)=>a.day.localeCompare(b.day));
    if(new Set(factors.map(f=>f.day)).size!==factors.length)throw Error("Duplicate adjustment day");
    return factors;
  }finally{client.destroy();}
}
export const archivedFactors=unstable_cache(queryFactors,["market-factors-raw-v1"],{revalidate:86400});
