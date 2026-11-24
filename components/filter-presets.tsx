"use client";
import {useEffect,useRef,useState} from "react";
import {Bookmark,Trash2} from "lucide-react";
import ToolbarFlyout from "@/components/toolbar-flyout";
export type FilterPreset={view:string;maxPrice:number|null;minCoverage:number;maxPb:number|null;includeMissingPb?:boolean;order:string;ascending:boolean;industry:string;watchOnly:boolean};
type Saved={name:string;filter:FilterPreset};
const KEY="value-radar-netnet-presets-v1";
const valid=(p:unknown):p is FilterPreset=>{
  if(!p||typeof p!=="object")return false;
  const v=p as FilterPreset;
  return ["all","strict","stress","gaps"].includes(v.view)&&(v.maxPrice===null||(typeof v.maxPrice==="number"&&Number.isFinite(v.maxPrice)&&v.maxPrice>=0))&&[1.25,1.5,1.75].includes(v.minCoverage)&&(v.maxPb===null||(typeof v.maxPb==="number"&&Number.isFinite(v.maxPb)))&&(v.includeMissingPb===undefined||typeof v.includeMissingPb==="boolean")&&["price","market_cap_yuan","ncav_coverage","stress_coverage","pb"].includes(v.order)&&typeof v.ascending==="boolean"&&typeof v.industry==="string"&&typeof v.watchOnly==="boolean";
};
function read():Saved[]{try{const raw=JSON.parse(localStorage.getItem(KEY)||"[]");return Array.isArray(raw)?raw.filter((x):x is Saved=>x&&typeof x.name==="string"&&x.name.length>0&&x.name.length<=30&&valid(x.filter)).slice(0,20):[];}catch{return [];}}
export default function FilterPresets({current,apply}:{current:FilterPreset;apply:(p:FilterPreset)=>void}){
  const [items,setItems]=useState<Saved[]>([]),[name,setName]=useState(""),[open,setOpen]=useState(false),[error,setError]=useState("");
  const anchor=useRef<HTMLDivElement>(null);
  useEffect(()=>{setItems(read());const close=()=>setOpen(false);window.addEventListener("a-share-strategy-change",close);return()=>window.removeEventListener("a-share-strategy-change",close);},[]);
  function persist(next:Saved[]){try{localStorage.setItem(KEY,JSON.stringify(next));setItems(next);setError("");return true;}catch{setError("无法保存浏览器设置");return false;}}
  function save(){const label=name.trim();if(!label||!valid(current))return;const next=[{name:label,filter:current},...items.filter(x=>x.name!==label)].slice(0,20);if(persist(next))setName("");}
  return <div className="column-control" ref={anchor}><button type="button" className="tool-button" aria-expanded={open} onClick={()=>{if(!open)setItems(read());setOpen(v=>!v);}}><Bookmark size={14}/> 方案</button>{open&&<ToolbarFlyout anchor={anchor} onDismiss={()=>setOpen(false)} align="right" width={280}><div className="v4-preset-menu column-menu" onKeyDown={e=>{if(e.key==="Escape")setOpen(false);}}><strong>筛选方案</strong><div className="v4-preset-entry"><input aria-label="方案名称" placeholder="输入方案名称" value={name} maxLength={30} onChange={e=>setName(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();save();}}}/><button type="button" className="tool-button" disabled={!name.trim()} onClick={save}>保存</button></div>{error&&<p role="alert">{error}</p>}{items.length?items.map(x=><div key={x.name} className="v4-preset-entry"><button className="v4-preset-apply" type="button" onClick={()=>{apply(x.filter);setOpen(false);}}>{x.name}</button><button type="button" className="watch-button" aria-label={"删除"+x.name} onClick={()=>persist(items.filter(i=>i.name!==x.name))}><Trash2 size={14}/></button></div>):<p className="muted">暂无已保存方案</p>}</div></ToolbarFlyout>}</div>;
}
