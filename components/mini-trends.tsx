"use client";
import {useEffect,useState} from "react";
type Point={day:string;close:number};
export function useMiniTrends(codes:string[],enabled:boolean){
  const key=enabled?[...new Set(codes)].slice(0,25).join(","):"";
  const [state,setState]=useState<{key:string;trends:Record<string,Point[]>;error:boolean}>({key:"",trends:{},error:false});
  useEffect(()=>{
    if(!key)return;
    const controller=new AbortController();
    fetch("/api/research/trends?codes="+encodeURIComponent(key),{signal:controller.signal})
      .then(async r=>{if(!r.ok)throw Error("trend");return (await r.json()).trends as Record<string,Point[]>;})
      .then(trends=>{if(!controller.signal.aborted)setState({key,trends,error:false});})
      .catch(()=>{if(!controller.signal.aborted)setState({key,trends:{},error:true});});
    return()=>controller.abort();
  },[key]);
  return state.key===key?{trends:state.trends,error:state.error,loading:false}:{trends:{},error:false,loading:!!key};
}
export function MiniTrend({points}:{points?:Point[]}){
  if(!points||points.length<2)return <span className="muted">—</span>;
  const values=points.map(p=>p.close),min=Math.min(...values),max=Math.max(...values),range=Math.max(.0001,max-min);
  const coords=points.map((p,i)=>[2+i/(points.length-1)*76, max===min?12:22-(p.close-min)/range*20].join(",")).join(" ");
  const color=values.at(-1)!>=values[0]?"#c93738":"#19805b";
  return <svg className="mini-trend" viewBox="0 0 80 24" role="img" aria-label={"近"+points.length+"个交易日收盘价走势"}><title>{points[0].day+" – "+points.at(-1)!.day}</title><polyline points={coords} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}
