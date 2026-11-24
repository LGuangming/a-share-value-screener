"use client";
import {useEffect,useRef,useState} from "react";
import Link from "next/link";
import {ArrowUpRight,Eye,X} from "lucide-react";
import {useResearchDetail} from "@/components/research-data";
import {ResearchMetrics} from "@/components/research-workspace";
import MarketChart from "@/components/market-chart";
import type {StockIdentity} from "@/lib/workspace-types";

export function PreviewButton({stock}:{stock:StockIdentity}){
  return <button type="button" className="watch-button" title="快速预览" aria-label={"预览"+stock.name} onClick={e=>{e.stopPropagation();window.dispatchEvent(new CustomEvent("value-radar-preview",{detail:stock}));}}><Eye size={16}/></button>;
}
export default function StockPreview(){
  const [stock,setStock]=useState<StockIdentity|null>(null);
  const dialog=useRef<HTMLDialogElement>(null);
  const {detail,error,loading}=useResearchDetail(stock?.ts_code??null);
  useEffect(()=>{const open=(e:Event)=>{setStock((e as CustomEvent<StockIdentity>).detail);dialog.current?.showModal();};window.addEventListener("value-radar-preview",open);return()=>window.removeEventListener("value-radar-preview",open);},[]);
  return <dialog ref={dialog} className="stock-preview" aria-label="个股快速预览" onClose={()=>setStock(null)} onClick={e=>{if(e.target===dialog.current)dialog.current?.close();}}>
    {stock&&<div className="preview-content"><header><strong>{stock.name} · {stock.ts_code}</strong><button type="button" className="tool-button" aria-label="关闭预览" onClick={()=>dialog.current?.close()}><X size={16}/></button></header><div className="preview-body">{loading?<p role="status">正在读取行情…</p>:error?<p role="alert">{error}</p>:detail?<><MarketChart key={stock.ts_code} code={stock.ts_code} name={stock.name} prices={detail.prices}/><ResearchMetrics detail={detail}/></>:null}</div><footer><Link className="tool-button" href={"/research?code="+encodeURIComponent(stock.ts_code)} onClick={()=>dialog.current?.close()}>打开行情工作台 <ArrowUpRight size={15}/></Link><Link className="tool-button" href={"/stocks/"+encodeURIComponent(stock.ts_code)} onClick={()=>dialog.current?.close()}>个股详情</Link></footer></div>}
  </dialog>;
}
