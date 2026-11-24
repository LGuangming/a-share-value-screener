"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, createSeriesMarkers, ColorType, CrosshairMode } from "lightweight-charts";
import { Maximize2, Minimize2, RotateCcw } from "lucide-react";
import {useWorkspacePreferences} from "@/components/workspace-preferences";
import { aggregateBars, completeBar, movingAverage, mergeArchivedBars, adjustBars, macd, rsi, boll, kdj, type Adjustment, type Factor, type MarketBar, type TradeMark } from "@/lib/market-bars";

const MA=[{period:5,color:"#D04A02"},{period:10,color:"#B58516"},{period:20,color:"#5475AC"},{period:50,color:"#8C66A4"},{period:60,color:"#217E83"},{period:120,color:"#7B8792"},{period:250,color:"#4E5660"}];
const UP="#c93738",DOWN="#19805b";
const money=(n:number|undefined)=>n===undefined?"—":n.toFixed(2);
const volume=(n:number|undefined)=>n===undefined?"—":n.toLocaleString("zh-CN",{maximumFractionDigits:2});
type Props={code:string;name:string;prices:MarketBar[];marks?:TradeMark[]};
const NO_MARKS:TradeMark[]=[];
const CURRENT_YEAR=new Date(Date.now()+8*3600000).getUTCFullYear();
const RANGES:Record<string,number[]>={[`2020-${CURRENT_YEAR}`]:Array.from({length:CURRENT_YEAR-2019},(_,i)=>2020+i),[`2023-${CURRENT_YEAR}`]:Array.from({length:CURRENT_YEAR-2022},(_,i)=>2023+i)};
const yearsFor=(source:string)=>RANGES[source]??[/^20\d{2}$/.test(source)&&Number(source)>=2020&&Number(source)<=CURRENT_YEAR?Number(source):0].filter(Boolean);

export default function MarketChart({code,name,marks=NO_MARKS}:Props){
  const {prefs}=useWorkspacePreferences();
  const dark=prefs.theme==="dark";
  const host=useRef<HTMLDivElement>(null);
  const root=useRef<HTMLElement>(null);
  const api=useRef<ReturnType<typeof createChart>|null>(null);
  const [source,setSource]=useState("recent");
  const [coverageNote,setCoverageNote]=useState("");
  const [archive,setArchive]=useState<MarketBar[]>([]);
  const [recentRange,setRecentRange]=useState<{from:string;to:string}|null>(null);
  const [loading,setLoading]=useState(true);
  const [progress,setProgress]=useState("");
  const [adjustment,setAdjustment]=useState<Adjustment>("unadjusted");
  const [factors,setFactors]=useState<{key:string;values:Factor[]}|null>(null);
  const [factorLoading,setFactorLoading]=useState(false);
  const [factorError,setFactorError]=useState("");
  const [showBoll,setShowBoll]=useState(false);
  const [indicator,setIndicator]=useState<"none"|"macd"|"rsi"|"kdj">("none");
  const factorCache=useRef(new Map<string,Factor[]>());
  const [error,setError]=useState("");
  const [period,setPeriod]=useState<"D"|"W"|"M">("D");
  const [kind,setKind]=useState<"candle"|"line">("candle");
  const [enabled,setEnabled]=useState([20,50]);
  const [showVolume,setShowVolume]=useState(true);
  const [hover,setHover]=useState<MarketBar|null>(null);
  const [expanded,setExpanded]=useState(false);
  const [chartError,setChartError]=useState(false);
  const requestCache=useRef(new Map<string,MarketBar[]>());

  useEffect(()=>{
    const controller=new AbortController();
    const years=yearsFor(source);
    setArchive([]);setRecentRange(null);setLoading(true);setError("");setProgress("");setCoverageNote("");
    async function load(){
      if(source==="recent"){
        const res=await fetch("/api/quotes/"+encodeURIComponent(code)+"?range=recent",{signal:controller.signal});
        const data=await res.json();
        if(!res.ok)throw Error(data.error||"近期行情暂不可用");
        if(data.code!==code||data.range!=="recent"||data.adjustment!=="unadjusted"||!Array.isArray(data.bars)||!data.coverage||typeof data.coverage.from!=="string"||typeof data.coverage.to!=="string"||data.coverage.archivedSessions>120||data.bars.length>120)throw Error("近期行情响应异常");
        if(!controller.signal.aborted){
          setArchive(mergeArchivedBars([data.bars]));
          setRecentRange({from:data.coverage.from,to:data.coverage.to});
          if(data.coverage.partial)setCoverageNote("已归档 "+data.coverage.archivedSessions+" 个市场交易日 · 最新 "+data.coverage.latest);
        }
        return;
      }
      const all:MarketBar[][]=[];
      for(let i=0;i<years.length;i+=2){
        const batch=await Promise.all(years.slice(i,i+2).map(async year=>{
          const key=code+":"+year;
          const stored=requestCache.current.get(key);
          if(stored&&year<2026)return stored;
          const res=await fetch("/api/quotes/"+encodeURIComponent(code)+"?year="+year,{signal:controller.signal});
          const data=await res.json();
          if(!res.ok)throw Error(year+"年："+(data.error||"历史行情暂不可用"));
          if(!Array.isArray(data.bars)||data.code!==code||data.year!==year||data.adjustment!=="unadjusted")throw Error("历史行情响应异常");
          const bars=data.bars as MarketBar[];
          if(year<2026)requestCache.current.set(key,bars);
          if(year>=2026&&data.coverage?.partial&&!controller.signal.aborted)setCoverageNote(year+"年归档尚未齐全 · 已归档 "+data.coverage.archivedSessions+" 个交易日 · 最新 "+data.coverage.latest);
          return bars;
        }));
        if(controller.signal.aborted)return;
        all.push(...batch);setProgress(all.length+"/"+years.length+" 年");
      }
      if(!controller.signal.aborted)setArchive(mergeArchivedBars(all));
    }
    load().catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"历史行情暂不可用");}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[source,code]);

  const factorKey=code+":"+source+(source==="recent"&&recentRange?":"+recentRange.from+":"+recentRange.to:"");
  useEffect(()=>{
    if(adjustment==="unadjusted"||(source==="recent"&&!recentRange)){setFactorLoading(false);setFactorError("");return;}
    const controller=new AbortController(),years=yearsFor(source);
    setFactorLoading(true);setFactorError("");
    async function load(){
      if(source==="recent"&&recentRange){
        const {from,to}=recentRange;
        const res=await fetch("/api/quotes/"+encodeURIComponent(code)+"/factors?range=recent&from="+encodeURIComponent(from)+"&to="+encodeURIComponent(to),{signal:controller.signal});
        const data=await res.json();
        if(!res.ok)throw Error(data.error||"近期复权因子暂不可用");
        if(data.code!==code||data.range!=="recent"||data.from!==from||data.to!==to||!Array.isArray(data.factors))throw Error("近期复权响应异常");
        if(!controller.signal.aborted)setFactors({key:factorKey,values:data.factors as Factor[]});
        return;
      }
      const all:Factor[]=[];
      for(let i=0;i<years.length;i+=2){
        const result=await Promise.all(years.slice(i,i+2).map(async year=>{
          const key=code+":"+year;
          const saved=factorCache.current.get(key);if(saved&&year<2026)return saved;
          const res=await fetch("/api/quotes/"+encodeURIComponent(code)+"/factors?year="+year,{signal:controller.signal});
          const data=await res.json();
          if(!res.ok)throw Error(year+"年："+(data.error||"复权因子暂不可用"));
          if(data.code!==code||data.year!==year||!Array.isArray(data.factors))throw Error("复权因子响应异常");
          const value=data.factors as Factor[];if(year<2026)factorCache.current.set(key,value);return value;
        }));
        if(controller.signal.aborted)return;
        all.push(...result.flat());
      }
      if(!controller.signal.aborted)setFactors({key:factorKey,values:all});
    }
    load().catch(e=>{if(!controller.signal.aborted)setFactorError(e instanceof Error?e.message:"复权因子暂不可用");}).finally(()=>{if(!controller.signal.aborted)setFactorLoading(false);});
    return ()=>controller.abort();
  },[source,code,adjustment,factorKey,recentRange]);

  const rawDaily=archive;
  const adjusted=useMemo(()=>{
    if(adjustment==="unadjusted")return {bars:rawDaily,error:""};
    if(factors?.key!==factorKey)return {bars:[] as MarketBar[],error:""};
    try{return {bars:adjustBars(rawDaily,factors.values,adjustment),error:""};}
    catch(e){return {bars:[] as MarketBar[],error:e instanceof Error?e.message:"复权失败"};}
  },[rawDaily,adjustment,factors,factorKey]);
  const daily=adjusted.bars;
  const busy=loading||factorLoading||(adjustment!=="unadjusted"&&factors?.key!==factorKey&&!factorError);
  const displayError=error||(adjustment!=="unadjusted"?(factorError||adjusted.error):"");
  const bars=useMemo(()=>aggregateBars(daily,period),[daily,period]);
  const bollData=useMemo(()=>showBoll?boll(bars):[],[bars,showBoll]);
  const lower=useMemo(()=>indicator==="macd"?macd(bars).map(p=>({time:p.time,a:p.dif,b:p.dea,c:p.histogram})):indicator==="rsi"?rsi(bars).map(p=>({time:p.time,a:p.value,b:undefined,c:undefined})):indicator==="kdj"?kdj(bars).map(p=>({time:p.time,a:p.k,b:p.d,c:p.j})):[],[bars,indicator]);
  const candles=bars.length>0&&bars.every(completeBar);
  const candleMode=candles&&kind==="candle";
  const hasVolume=bars.some(b=>typeof b.volume==="number");
  const shown=busy||displayError?undefined:hover??bars.at(-1);
  const indicatorPoint=shown?lower.find(p=>p.time===shown.day):undefined;
  useEffect(()=>{
    setHover(null);setChartError(false);
    if(!host.current||!bars.length||busy||displayError)return;
    const chart=createChart(host.current,{
      autoSize:true,
      layout:{background:{type:ColorType.Solid,color:dark?"#1b2027":"#ffffff"},textColor:dark?"#bbc3cf":"#606770",fontSize:12,attributionLogo:true},
      grid:{vertLines:{color:dark?"#303740":"#f0f1f2"},horzLines:{color:dark?"#303740":"#e9ecef"}},
      crosshair:{mode:CrosshairMode.Normal},
      rightPriceScale:{borderColor:dark?"#3b444f":"#dfe3e7",scaleMargins:{top:.12,bottom:showVolume && hasVolume ? .24 : .09}},
      timeScale:{borderColor:dark?"#3b444f":"#dfe3e7",timeVisible:false,secondsVisible:false},
      localization:{locale:"zh-CN"},
    });
    api.current=chart;
    try{
      const visibleMarks=period==="D"?marks.filter(m=>bars.some(b=>b.day===m.day)).sort((a,b)=>a.day.localeCompare(b.day)).map(m=>({
        time:m.day,position:m.side==="buy"?"belowBar" as const:"aboveBar" as const,
        color:m.side==="buy"?UP:DOWN,shape:m.side==="buy"?"arrowUp" as const:"arrowDown" as const,text:m.label
      })):[];
      if(candleMode){
        const series=chart.addSeries(CandlestickSeries,{upColor:UP,downColor:DOWN,borderVisible:false,wickUpColor:UP,wickDownColor:DOWN});
        series.setData(bars.filter(completeBar).map(b=>({time:b.day,open:b.open,high:b.high,low:b.low,close:b.close})));
        if(visibleMarks.length)createSeriesMarkers(series,visibleMarks);
      }else{
        const series=chart.addSeries(LineSeries,{color:"#D04A02",lineWidth:2,priceLineVisible:true});
        series.setData(bars.map(b=>({time:b.day,value:b.close})));
        if(visibleMarks.length)createSeriesMarkers(series,visibleMarks);
      }
      for(const ma of MA.filter(m=>enabled.includes(m.period))){
        const line=chart.addSeries(LineSeries,{color:ma.color,lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
        line.setData(movingAverage(bars,ma.period));
      }
      if(showBoll&&bollData.length){
        for(const [key,color] of [["middle","#B58516"],["upper","#8293a6"],["lower","#8293a6"]] as const){
          const line=chart.addSeries(LineSeries,{color,lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
          line.setData(bollData.map(p=>({time:p.time,value:p[key]})));
        }
      }
      if(indicator!=="none"&&lower.length){
        const a=chart.addSeries(LineSeries,{color:"#D04A02",lineWidth:1,priceLineVisible:false,lastValueVisible:false},1);
        a.setData(lower.map(p=>({time:p.time,value:p.a})));
        if(indicator!=="rsi"){
          const b=chart.addSeries(LineSeries,{color:"#5475AC",lineWidth:1,priceLineVisible:false,lastValueVisible:false},1);
          b.setData(lower.flatMap(p=>p.b===undefined?[]:[{time:p.time,value:p.b}]));
        }
        if(indicator==="macd"){
          const h=chart.addSeries(HistogramSeries,{priceLineVisible:false,lastValueVisible:false},1);
          h.setData(lower.flatMap(p=>p.c===undefined?[]:[{time:p.time,value:p.c,color:p.c>=0?UP:DOWN}]));
        }else if(indicator==="kdj"){
          const j=chart.addSeries(LineSeries,{color:"#8C66A4",lineWidth:1,priceLineVisible:false,lastValueVisible:false},1);
          j.setData(lower.flatMap(p=>p.c===undefined?[]:[{time:p.time,value:p.c}]));
        }
      }
      if(showVolume&&hasVolume){
        const vol=chart.addSeries(HistogramSeries,{priceFormat:{type:"volume"},priceScaleId:"volume",priceLineVisible:false,lastValueVisible:false});
        vol.priceScale().applyOptions({scaleMargins:{top:.79,bottom:0}});
        vol.setData(bars.flatMap((b,i)=>typeof b.volume==="number"?[{time:b.day,value:b.volume,color:(b.open!==undefined?b.close>=b.open:i===0||b.close>=bars[i-1].close)?"#c9373870":"#19805b70"}]:[]));
      }
      const map=new Map(bars.map(b=>[b.day,b]));
      chart.subscribeCrosshairMove(p=>setHover(typeof p.time==="string"?map.get(p.time)??null:null));
      chart.timeScale().fitContent();
    }catch{
      setChartError(true);
    }
    return ()=>{api.current=null;chart.remove();};
  },[bars,candleMode,enabled,showVolume,hasVolume,busy,displayError,period,marks,dark,showBoll,bollData,indicator,lower]);

  useEffect(()=>{
    const handle=()=>{if(!document.fullscreenElement)setExpanded(false);};
    const key=(e:KeyboardEvent)=>{if(e.key==="Escape")setExpanded(false);};
    document.addEventListener("fullscreenchange",handle);
    window.addEventListener("keydown",key);
    return ()=>{document.removeEventListener("fullscreenchange",handle);window.removeEventListener("keydown",key);};
  },[]);
  async function toggleExpanded(){
    if(expanded){
      if(document.fullscreenElement===root.current)await document.exitFullscreen().catch(()=>{});
      setExpanded(false);return;
    }
    setExpanded(true);
    if(root.current?.requestFullscreen)await root.current.requestFullscreen().catch(()=>{});
  }

  function chooseSource(value:string){if(value!==source){setHover(null);setArchive([]);setError("");setLoading(true);setRecentRange(null);setKind("candle");setAdjustment("unadjusted");setFactorError("");setFactorLoading(false);setSource(value);}}
  return <section ref={root} className={"market-chart-shell"+(expanded?" chart-expanded":"")} aria-label={name+"行情分析"}>
    <div className="chart-toolbar">
      <div className="chart-group"><select aria-label="行情数据区间" value={source} onChange={e=>chooseSource(e.target.value)}>
        <option value="recent">近期行情（120个交易日）</option><option value={`2020-${CURRENT_YEAR}`}>2020–{CURRENT_YEAR} 连续行情</option><option value={`2023-${CURRENT_YEAR}`}>2023–{CURRENT_YEAR} 连续行情</option>{Array.from({length:CURRENT_YEAR-2019},(_,i)=>CURRENT_YEAR-i).map(y=><option key={y} value={y}>{y}年归档</option>)}
      </select></div>
      <div className="chart-group"><select aria-label="复权方式" value={adjustment} disabled={loading||!archive.length} onChange={e=>{setHover(null);setFactorError("");setAdjustment(e.target.value as Adjustment);}}><option value="unadjusted">不复权</option><option value="forward">前复权（区间末）</option><option value="backward">后复权（区间首）</option></select></div>
      <div className="chart-group" aria-label="行情周期">{(["D","W","M"] as const).map((p,i)=><button type="button" key={p} aria-pressed={period===p} onClick={()=>setPeriod(p)}>{["日线","周线","月线"][i]}</button>)}</div>
      <div className="chart-group"><button type="button" aria-pressed={candleMode} disabled={!candles} onClick={()=>setKind("candle")}>K线</button><button type="button" aria-pressed={!candleMode} onClick={()=>setKind("line")}>收盘价</button></div>
      <div className="chart-actions"><button type="button" title="重置视图" aria-label="重置视图" onClick={()=>api.current?.timeScale().fitContent()}><RotateCcw size={15}/></button>
      <button type="button" title={expanded?"退出大图":"放大图表"} aria-label={expanded?"退出大图":"放大图表"} onClick={toggleExpanded}>{expanded?<Minimize2 size={17}/>:<Maximize2 size={17}/>}</button></div>
    </div>
    <div className="chart-indicators">
      {MA.map(m=><button type="button" key={m.period} style={{"--ma-color":m.color} as React.CSSProperties} aria-pressed={enabled.includes(m.period)} onClick={()=>setEnabled(prev=>prev.includes(m.period)?prev.filter(x=>x!==m.period):[...prev,m.period])}>MA{m.period}</button>)}
      <button type="button" aria-pressed={showBoll} onClick={()=>setShowBoll(v=>!v)}>BOLL(20,2)</button><select aria-label="副图指标" value={indicator} onChange={e=>setIndicator(e.target.value as typeof indicator)}><option value="none">副图指标</option><option value="macd">MACD(12,26,9)</option><option value="rsi">RSI(14)</option><option value="kdj" disabled={!candles}>KDJ(9,3,3)</option></select>
      <button type="button" aria-pressed={showVolume&&hasVolume} disabled={!hasVolume} onClick={()=>setShowVolume(v=>!v)}>成交量</button>
    </div>
    {!busy&&!displayError&&((indicator!=="none"&&!lower.length)||(showBoll&&!bollData.length))&&<div className="chart-indicator-status" role="status">当前周期历史记录不足，部分指标不可用</div>}
    <div className="chart-quote-strip" aria-label="光标对应行情">
      <strong>{shown?.day??"—"}</strong><span>开 {money(shown?.open)}</span><span>高 {money(shown?.high)}</span><span>低 {money(shown?.low)}</span><span>收 {money(shown?.close)}</span><span>量 {volume(shown?.volume)} 手</span>
    </div>
    {indicator!=="none"&&lower.length>0&&!busy&&!displayError&&<div className="chart-indicator-values" aria-label="光标对应技术指标"><strong>{indicator.toUpperCase()}</strong><span style={{color:"#D04A02"}}>{indicator==="macd"?"DIF":indicator==="kdj"?"K":"RSI"} {money(indicatorPoint?.a)}</span>{indicator!=="rsi"&&<span style={{color:"#5475AC"}}>{indicator==="macd"?"DEA":"D"} {money(indicatorPoint?.b)}</span>}{indicator!=="rsi"&&<span style={{color:indicator==="macd"?(indicatorPoint?.c??0)>=0?UP:DOWN:"#8C66A4"}}>{indicator==="macd"?"MACD":"J"} {money(indicatorPoint?.c)}</span>}</div>}
    <div className="chart-canvas-wrap">
      <div className="chart-canvas" ref={host} style={{visibility:busy||displayError||!bars.length||chartError?"hidden":"visible"}}/>
      {(busy||displayError||!bars.length||chartError)&&<div className="chart-placeholder" role="status">{busy?<><span className="chart-spinner"/>{factorLoading?"正在读取复权因子":"正在读取已归档行情"} {loading?progress:""}</>:displayError|| (chartError?"行情图暂不可用":"当前区间暂无行情")}</div>}
    </div>
    <div className="chart-footer"><span>{source==="recent"?"近期归档行情":source+"年历史行情"} · {adjustment==="unadjusted"?"不复权":adjustment==="forward"?"前复权（区间末）":"后复权（区间首）"} · {daily.length} 个交易日{coverageNote?" · "+coverageNote:""}</span><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™ · © 2025 TradingView, Inc.</a></div>
  </section>;
}
