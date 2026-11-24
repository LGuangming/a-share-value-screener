"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, createSeriesMarkers, ColorType, CrosshairMode } from "lightweight-charts";
import { Maximize2, Minimize2, RotateCcw } from "lucide-react";
import { aggregateBars, completeBar, movingAverage, type MarketBar, type TradeMark } from "@/lib/market-bars";

const MA=[{period:5,color:"#D04A02"},{period:10,color:"#B58516"},{period:20,color:"#5475AC"},{period:50,color:"#8C66A4"},{period:60,color:"#217E83"},{period:120,color:"#7B8792"},{period:250,color:"#4E5660"}];
const UP="#c93738",DOWN="#19805b";
const money=(n:number|undefined)=>n===undefined?"—":n.toFixed(2);
const volume=(n:number|undefined)=>n===undefined?"—":n.toLocaleString("zh-CN",{maximumFractionDigits:2});
type Props={code:string;name:string;prices:MarketBar[];marks?:TradeMark[]};
const NO_MARKS:TradeMark[]=[];

export default function MarketChart({code,name,prices,marks=NO_MARKS}:Props){
  const host=useRef<HTMLDivElement>(null);
  const root=useRef<HTMLElement>(null);
  const api=useRef<ReturnType<typeof createChart>|null>(null);
  const [source,setSource]=useState("recent");
  const [archive,setArchive]=useState<MarketBar[]>([]);
  const [loading,setLoading]=useState(false);
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
    if(source==="recent"){setLoading(false);setError("");return;}
    const key=code+":"+source;
    const stored=requestCache.current.get(key);
    if(stored){setArchive(stored);setLoading(false);setError("");return;}
    const controller=new AbortController();
    setArchive([]);setLoading(true);setError("");
    fetch("/api/quotes/"+encodeURIComponent(code)+"?year="+source,{signal:controller.signal})
      .then(async res=>{const data=await res.json();if(!res.ok)throw new Error(data.error||"历史行情暂不可用");if(!Array.isArray(data.bars)||data.code!==code||String(data.year)!==source)throw new Error("历史行情响应异常");return data.bars as MarketBar[];})
      .then(bars=>{if(!controller.signal.aborted){requestCache.current.set(key,bars);setArchive(bars);}})
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"历史行情暂不可用");})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[source,code]);

  const daily=source==="recent"?prices:archive;
  const bars=useMemo(()=>aggregateBars(daily,period),[daily,period]);
  const candles=bars.length>0&&bars.every(completeBar);
  const candleMode=candles&&kind==="candle";
  const hasVolume=bars.some(b=>typeof b.volume==="number");
  const shown=hover??bars.at(-1);
  useEffect(()=>{
    setHover(null);setChartError(false);
    if(!host.current||!bars.length||loading||error)return;
    const chart=createChart(host.current,{
      autoSize:true,
      layout:{background:{type:ColorType.Solid,color:"#ffffff"},textColor:"#606770",fontSize:12,attributionLogo:true},
      grid:{vertLines:{color:"#f0f1f2"},horzLines:{color:"#e9ecef"}},
      crosshair:{mode:CrosshairMode.Normal},
      rightPriceScale:{borderColor:"#dfe3e7",scaleMargins:{top:.12,bottom:showVolume && hasVolume ? .24 : .09}},
      timeScale:{borderColor:"#dfe3e7",timeVisible:false,secondsVisible:false},
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
  },[bars,candleMode,enabled,showVolume,hasVolume,loading,error,period,marks]);

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

  function chooseSource(value:string){if(value!==source){setHover(null);setArchive([]);setError("");setLoading(value!=="recent");setSource(value);}}
  return <section ref={root} className={"market-chart-shell"+(expanded?" chart-expanded":"")} aria-label={name+"行情分析"}>
    <div className="chart-toolbar">
      <div className="chart-group"><select aria-label="行情数据区间" value={source} onChange={e=>chooseSource(e.target.value)}>
        <option value="recent">近期行情</option>{[2025,2024,2023,2022,2021,2020].map(y=><option key={y} value={y}>{y}年归档</option>)}
      </select></div>
      <div className="chart-group" aria-label="行情周期">{(["D","W","M"] as const).map((p,i)=><button type="button" key={p} aria-pressed={period===p} onClick={()=>setPeriod(p)}>{["日线","周线","月线"][i]}</button>)}</div>
      <div className="chart-group"><button type="button" aria-pressed={candleMode} disabled={!candles} onClick={()=>setKind("candle")}>K线</button><button type="button" aria-pressed={!candleMode} onClick={()=>setKind("line")}>收盘价</button></div>
      <div className="chart-actions"><button type="button" title="重置视图" aria-label="重置视图" onClick={()=>api.current?.timeScale().fitContent()}><RotateCcw size={15}/></button>
      <button type="button" title={expanded?"退出大图":"放大图表"} aria-label={expanded?"退出大图":"放大图表"} onClick={toggleExpanded}>{expanded?<Minimize2 size={17}/>:<Maximize2 size={17}/>}</button></div>
    </div>
    <div className="chart-indicators">
      {MA.map(m=><button type="button" key={m.period} style={{"--ma-color":m.color} as React.CSSProperties} aria-pressed={enabled.includes(m.period)} onClick={()=>setEnabled(prev=>prev.includes(m.period)?prev.filter(x=>x!==m.period):[...prev,m.period])}>MA{m.period}</button>)}
      <button type="button" aria-pressed={showVolume&&hasVolume} disabled={!hasVolume} onClick={()=>setShowVolume(v=>!v)}>成交量</button>
    </div>
    <div className="chart-quote-strip" aria-label="光标对应行情">
      <strong>{shown?.day??"—"}</strong><span>开 {money(shown?.open)}</span><span>高 {money(shown?.high)}</span><span>低 {money(shown?.low)}</span><span>收 {money(shown?.close)}</span><span>量 {volume(shown?.volume)} 手</span>
    </div>
    <div className="chart-canvas-wrap">
      <div className="chart-canvas" ref={host} style={{visibility:loading||error||!bars.length||chartError?"hidden":"visible"}}/>
      {(loading||error||!bars.length||chartError)&&<div className="chart-placeholder" role="status">{loading?<><span className="chart-spinner"/>正在读取已归档行情</>:error|| (chartError?"行情图暂不可用":"当前区间暂无行情")}</div>}
    </div>
    <div className="chart-footer"><span>{source==="recent"?"近期收盘价":source+"年历史行情"} · 不复权 · {daily.length} 个交易日</span><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™ · © 2025 TradingView, Inc.</a></div>
  </section>;
}
