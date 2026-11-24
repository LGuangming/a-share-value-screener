"use client";
import {useEffect,useState} from "react";
import type {WorkspaceDetail,StockIdentity} from "@/lib/workspace-types";

export function useResearchDetail(code:string|null){
  const [state,setState]=useState<{code:string|null;detail:WorkspaceDetail|null;error:string}>({code:null,detail:null,error:""});
  useEffect(()=>{
    if(!code)return;
    const controller=new AbortController();
    fetch("/api/research/"+encodeURIComponent(code),{signal:controller.signal})
      .then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error||"研究数据暂不可用");if(d.identity?.ts_code!==code)throw Error("股票响应不匹配");return d as WorkspaceDetail;})
      .then(d=>{if(!controller.signal.aborted)setState({code,detail:d,error:""});})
      .catch(e=>{if(!controller.signal.aborted)setState({code,detail:null,error:e instanceof Error?e.message:"研究数据暂不可用"});});
    return()=>controller.abort();
  },[code]);
  return state.code===code?{detail:state.detail,error:state.error,loading:false}:{detail:null,error:"",loading:!!code};
}
export function useStockSearch(query:string,enabled=true){
  const q=query.trim();
  const [result,setResult]=useState<{q:string;stocks:StockIdentity[];error:string}>({q:"",stocks:[],error:""});
  useEffect(()=>{
    if(!enabled||!q||q.length>40)return;
    const controller=new AbortController();
    const timer=setTimeout(()=>{
      fetch("/api/research/search?q="+encodeURIComponent(q),{signal:controller.signal})
        .then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error||"搜索失败");return d.stocks as StockIdentity[];})
        .then(stocks=>{if(!controller.signal.aborted)setResult({q,stocks,error:""});})
        .catch(e=>{if(!controller.signal.aborted)setResult({q,stocks:[],error:e instanceof Error?e.message:"搜索失败"});});
    },250);
    return()=>{clearTimeout(timer);controller.abort();};
  },[q,enabled]);
  return enabled&&q&&result.q===q?{stocks:result.stocks,error:result.error,loading:false}:{stocks:[],error:"",loading:enabled&&!!q};
}
