"use client";
import {useEffect,useState,useCallback,type ReactNode} from "react";
import {Moon,Sun,PanelLeftClose,PanelLeftOpen,Rows3,Rows4,Star} from "lucide-react";
import type {StockIdentity} from "@/lib/workspace-types";
type Prefs={theme:"light"|"dark";density:"standard"|"compact";collapsed:boolean};
const defaults:Prefs={theme:"light",density:"standard",collapsed:false};
const PREFS="value-radar-ui-v3",WATCH="value-radar-watchlist-v1";
function read<T>(key:string,fallback:T):T{try{return JSON.parse(localStorage.getItem(key)||"null")??fallback;}catch{return fallback;}}
function write(key:string,value:unknown){try{localStorage.setItem(key,JSON.stringify(value));}catch{}window.dispatchEvent(new Event(key));}
export function useWorkspacePreferences(){
  const [prefs,setPrefs]=useState<Prefs>(defaults);
  useEffect(()=>{const sync=()=>{const p=read<Partial<Prefs>>(PREFS,defaults);setPrefs({theme:p.theme==="dark"?"dark":"light",density:p.density==="compact"?"compact":"standard",collapsed:p.collapsed===true});};sync();window.addEventListener(PREFS,sync);window.addEventListener("storage",sync);return()=>{window.removeEventListener(PREFS,sync);window.removeEventListener("storage",sync);};},[]);
  const update=useCallback((patch:Partial<Prefs>)=>write(PREFS,{...read(PREFS,defaults),...patch}),[]);
  return {prefs,update};
}
export function PreferencesRoot({children}:{children:ReactNode}){
  const {prefs}=useWorkspacePreferences();
  useEffect(()=>{const root=document.documentElement;root.dataset.theme=prefs.theme;root.dataset.density=prefs.density;root.dataset.sidebar=prefs.collapsed?"collapsed":"expanded";},[prefs]);
  return <>{children}</>;
}
export function AppearanceControls(){
  const {prefs,update}=useWorkspacePreferences();
  return <div className="appearance-controls">
    <button type="button" className="appearance-button" title={prefs.theme==="dark"?"浅色模式":"深色模式"} aria-label="切换浅色/深色模式" aria-pressed={prefs.theme==="dark"} onClick={()=>update({theme:prefs.theme==="dark"?"light":"dark"})}>{prefs.theme==="dark"?<Sun size={17}/>:<Moon size={17}/>}</button>
    <button type="button" className="appearance-button" title="切换信息密度" aria-label="紧凑显示" aria-pressed={prefs.density==="compact"} onClick={()=>update({density:prefs.density==="compact"?"standard":"compact"})}>{prefs.density==="compact"?<Rows3 size={17}/>:<Rows4 size={17}/>}</button>
    <button type="button" className="appearance-button collapse-nav" title={prefs.collapsed?"展开导航":"收起导航"} aria-label="切换导航栏" aria-pressed={prefs.collapsed} onClick={()=>update({collapsed:!prefs.collapsed})}>{prefs.collapsed?<PanelLeftOpen size={17}/>:<PanelLeftClose size={17}/>}</button>
  </div>;
}
function normalizeWatch(value:unknown):StockIdentity[]{
  return Array.isArray(value)?value.filter((x):x is StockIdentity=>Boolean(x&&typeof x.ts_code==="string"&&/^\d{6}\.(SH|SZ|BJ)$/.test(x.ts_code)&&typeof x.name==="string")).slice(0,300):[];
}
export function useWatchlist(){
  const [items,setItems]=useState<StockIdentity[]>([]);
  useEffect(()=>{const sync=()=>setItems(normalizeWatch(read<unknown>(WATCH,[])));sync();window.addEventListener(WATCH,sync);window.addEventListener("storage",sync);return()=>{window.removeEventListener(WATCH,sync);window.removeEventListener("storage",sync);};},[]);
  const toggle=useCallback((stock:StockIdentity)=>{
    const current=normalizeWatch(read<unknown>(WATCH,[]));
    write(WATCH,current.some(x=>x.ts_code===stock.ts_code)?current.filter(x=>x.ts_code!==stock.ts_code):[...current,stock].slice(-300));
  },[]);
  return {items,toggle};
}
export function WatchButton({stock}:{stock:StockIdentity}){
  const {items,toggle}=useWatchlist();
  const active=items.some(x=>x.ts_code===stock.ts_code);
  return <button type="button" className={"watch-button"+(active?" is-watched":"")} title={active?"取消自选":"加入自选"} aria-label={(active?"取消关注":"关注")+stock.name} aria-pressed={active} onClick={e=>{e.stopPropagation();toggle(stock);}}><Star size={15} fill={active?"currentColor":"none"}/></button>;
}
