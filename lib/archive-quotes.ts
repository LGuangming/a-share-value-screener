import "server-only";
import { createHash } from "node:crypto";
import {storage,requiredObject,archiveCache,ArchiveUnavailable} from "./archive-storage";
export {ArchiveUnavailable} from "./archive-storage";
import { parquetReadObjects } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import { completeBar, recentWindow, recentMonthRange, RECENT_SESSIONS, type MarketBar, type Factor } from "./market-bars";

import { RAW_PREFIX as PREFIX } from "./archive-paths";
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
async function queryYear(code:string,year:number,selectedMonths?:string[],sessionDates?:Set<string>):Promise<MarketBar[]>{
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!Number.isInteger(year)||year<2020||year>2025)throw new Error("Invalid query");
  const s=storage();
  const get=(key:string,max:number)=>requiredObject(s,key,max,15000);
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
    const selected=selectedMonths?parts.filter(p=>selectedMonths.includes(p.month)):parts;
    for(let i=0;i<selected.length;i+=2){
      const result=await Promise.all(selected.slice(i,i+2).map(async p=>{
        const bytes=await get(PREFIX+"/"+p.path,p.bytes);
        if(bytes.byteLength!==p.bytes||createHash("sha256").update(bytes).digest("hex")!==p.sha256)throw new Error("Archive checksum mismatch");
        const file=new Uint8Array(bytes).buffer;
        const records=await parquetReadObjects({file,compressors,columns:["ts_code","trade_date","open","high","low","close","vol","amount"]});
        if(records.length!==p.rows)throw new Error("Archive row count mismatch");
        if(sessionDates){for(const r of records){const d=isoDay(r.trade_date);if(!d.startsWith(year+"-"+p.month+"-"))throw Error("Invalid archive session");sessionDates.add(d);}}
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
  } finally {s.close();}
}
export const archivedQuotes=archiveCache((code:string,year:number)=>queryYear(code,year),"market-archive-raw-v1",86400);

async function queryFactors(code:string,year:number,selectedMonths?:string[]):Promise<Factor[]>{
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!Number.isInteger(year)||year<2020||year>2025)throw Error("Invalid factor query");
  const s=storage();
  const prefix=PREFIX+"/adj_factor/year="+year;
  const get=(key:string,max:number)=>requiredObject(s,key,max,15000);
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
    const selected=selectedMonths?doc.months.filter((p:{month:string})=>selectedMonths.includes(p.month)):doc.months;
    for(let i=0;i<selected.length;i+=2){
      const parts=await Promise.all(selected.slice(i,i+2).map(async (p:{object_key:string;parquet_bytes:number;rows:number;month:string;sha256:string})=>{
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
  }finally{s.close();}
}
export const archivedFactors=archiveCache((code:string,year:number)=>queryFactors(code,year),"market-factors-raw-v1",86400);


// 2026+ partial-year reader: read only published, content-addressed monthly
// snapshots; never pretend that missing months or sessions were archived.
type MonthReader={format_version:number;status:string;dataset:string;year:number;month:string;through:string;dates:string[];expected_dates:string[];missing_dates:string[];rows:number;object_key:string;bytes:number;sha256:string;fields:string[];source_sha256:string[]};
type Coverage={months:number;archivedSessions:number;missingMonths:string[];missingSessions:string[];latest:string;through:string;partial:boolean};
async function readIncremental(code:string,year:number,dataset:"daily_prices"|"adj_factor",selectedMonths?:string[]){
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code)||!Number.isInteger(year)||year<2026||year>new Date(Date.now()+8*3600000).getUTCFullYear())throw Error("Invalid incremental query");
  const s=storage();
  const now=new Date(Date.now()+8*3600000);
  const lastMonth=year===now.getUTCFullYear()?now.getUTCMonth()+1:12;
  const months=selectedMonths??Array.from({length:lastMonth},(_,i)=>String(i+1).padStart(2,"0"));
  const dayPattern=/^\d{4}-\d{2}-\d{2}$/;
  const wanted=dataset==="daily_prices"?["ts_code","trade_date","open","high","low","close","vol","amount"]:["ts_code","trade_date","adj_factor"];
  const get=async(key:string,max:number,optional=false)=>optional?await s.get(key,max,15000):await requiredObject(s,key,max,15000);
  const bars:MarketBar[]=[], factors:Factor[]=[], dates:string[]=[], missingMonths:string[]=[],missingSessions:string[]=[];
  let count=0,through="";
  try{
    for(let i=0;i<months.length;i+=2){
      const loaded=await Promise.all(months.slice(i,i+2).map(async month=>{
        const root=PREFIX+"/"+dataset+"/year="+year+"/month="+month;
        const raw=await get(root+"/reader.manifest.json",256*1024,true);
        if(!raw)return {month,doc:null,records:[]};
        const doc=JSON.parse(new TextDecoder().decode(raw)) as MonthReader;
        const validDates=(v:unknown):v is string[]=>Array.isArray(v)&&v.every(d=>typeof d==="string"&&dayPattern.test(d)&&d.startsWith(year+"-"+month+"-"))&&new Set(v).size===v.length&&v.every((d,i)=>!i||d>v[i-1]);
        if(doc.format_version!==1||doc.status!=="verified_month_reader"||doc.dataset!==dataset||doc.year!==year||doc.month!==month||!dayPattern.test(doc.through)||!doc.through.startsWith(year+"-"+month+"-")||!validDates(doc.dates)||!doc.dates.length||!validDates(doc.expected_dates)||!validDates(doc.missing_dates)||!doc.dates.every(d=>doc.expected_dates.includes(d))||doc.expected_dates.some(d=>d>doc.through)||doc.missing_dates.join(",")!==doc.expected_dates.filter(d=>!doc.dates.includes(d)).join(",")||!Array.isArray(doc.fields)||doc.fields.join(",")!==wanted.join(",")||!Number.isSafeInteger(doc.rows)||doc.rows<=0||!Number.isSafeInteger(doc.bytes)||doc.bytes<=0||doc.bytes>16*1024*1024||!/^[a-f0-9]{64}$/.test(doc.sha256)||doc.object_key!==root+"/reader/sha256="+doc.sha256+".parquet"||!Array.isArray(doc.source_sha256)||doc.source_sha256.length!==doc.dates.length||doc.source_sha256.some(s=>!/^[a-f0-9]{64}$/.test(s)))throw Error("Invalid incremental month manifest");
        const bytes=await get(doc.object_key,doc.bytes);
        if(!bytes||bytes.length!==doc.bytes||createHash("sha256").update(bytes).digest("hex")!==doc.sha256)throw Error("Incremental checksum mismatch");
        const records=await parquetReadObjects({file:new Uint8Array(bytes).buffer,compressors,columns:wanted});
        if(records.length!==doc.rows)throw Error("Incremental row count mismatch");
        return {month,doc,records:records.filter(r=>r.ts_code===code)};
      }));
      for(const {month,doc,records} of loaded){
        if(!doc){missingMonths.push(month);continue;}
        count++;dates.push(...doc.dates);missingSessions.push(...doc.missing_dates);
        if(doc.through>through)through=doc.through;
        for(const r of records){
          const day=isoDay(r.trade_date);
          if(!doc.dates.includes(day))throw Error("Incremental date outside manifest");
          if(dataset==="daily_prices"){
            const b={day,open:number(r.open),high:number(r.high),low:number(r.low),close:number(r.close),volume:number(r.vol),amount:number(r.amount)};
            if(!completeBar(b)||b.volume<0||b.amount<0)throw Error("Invalid incremental bar");
            bars.push(b);
          }else{const factor=number(r.adj_factor);if(factor<=0)throw Error("Invalid incremental factor");factors.push({day,factor});}
        }
      }
    }
    if(!count&&!selectedMonths)throw new ArchiveUnavailable("该年度尚无已验证行情归档");
    bars.sort((a,b)=>a.day.localeCompare(b.day));factors.sort((a,b)=>a.day.localeCompare(b.day));
    if(new Set(bars.map(b=>b.day)).size!==bars.length||new Set(factors.map(b=>b.day)).size!==factors.length)throw Error("Duplicate incremental day");
    const latest=dates.at(-1)??"";
    const coverage:Coverage={months:count,archivedSessions:dates.length,missingMonths,missingSessions,latest,through,partial:!!(missingMonths.length||missingSessions.length)};
    return {bars,factors,coverage,dates};
  }finally{s.close();}
}
export const incrementalQuotes=archiveCache((code:string,year:number)=>readIncremental(code,year,"daily_prices"),"market-incremental-quotes-v1",60);
export const incrementalFactors=archiveCache((code:string,year:number)=>readIncremental(code,year,"adj_factor"),"market-incremental-factors-v1",60);


// Read the newest verified monthly partitions first, stopping once 120 market
// sessions are covered. This avoids loading the full current and previous years
// for the default chart and works at month/year boundaries.
async function queryRecent(code:string){
  const now=new Date(Date.now()+8*3600000);
  const cursor=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),1));
  const sessions=new Set<string>(),bars:MarketBar[]=[],missingMonths:string[]=[],missingSessions:string[]=[];
  for(let i=0;i<18&&sessions.size<RECENT_SESSIONS&&cursor.getUTCFullYear()>=2020;i++){
    const year=cursor.getUTCFullYear(),month=String(cursor.getUTCMonth()+1).padStart(2,"0");
    if(year>=2026){
      const result=await readIncremental(code,year,"daily_prices",[month]);
      result.dates.forEach(d=>sessions.add(d));bars.push(...result.bars);
      missingMonths.push(...result.coverage.missingMonths.map(m=>year+"-"+m));
      missingSessions.push(...result.coverage.missingSessions);
    }else{
      bars.push(...await queryYear(code,year,[month],sessions));
    }
    cursor.setUTCMonth(cursor.getUTCMonth()-1);
  }
  const window=recentWindow(bars,[...sessions]);
  if(!window.dates.length)throw new ArchiveUnavailable("近期暂无已验证行情归档");
  const gaps=missingSessions.filter(d=>d>=window.from);
  return {bars:window.bars,coverage:{from:window.from,to:window.to,latest:window.to,
    archivedSessions:window.dates.length,missingMonths,missingSessions:gaps,
    partial:window.dates.length<RECENT_SESSIONS||!!(missingMonths.length||gaps.length)}};
}
export const recentQuotes=archiveCache(queryRecent,"market-recent-120-v1",60);

async function queryRecentFactors(code:string,from:string,to:string){
  const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
  const months=recentMonthRange(from,to);
  if(to>today)throw Error("Future factor range");
  const groups=new Map<number,string[]>();
  for(const {year,month} of months){const list=groups.get(year)??[];list.push(month);groups.set(year,list);}
  const factors:Factor[]=[];
  for(const [year,months] of groups){
    if(year>=2026)factors.push(...(await readIncremental(code,year,"adj_factor",months)).factors);
    else factors.push(...await queryFactors(code,year,months));
  }
  const result=factors.filter(f=>f.day>=from&&f.day<=to).sort((a,b)=>a.day.localeCompare(b.day));
  if(new Set(result.map(f=>f.day)).size!==result.length)throw Error("Duplicate recent factor");
  return result;
}
export const recentFactors=archiveCache(queryRecentFactors,"market-recent-factors-v1",60);
