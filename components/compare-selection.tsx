"use client";
import {useState} from "react";
import Link from "next/link";
import {Columns3,X} from "lucide-react";
export function useCompareSelection(){
  const [codes,setCodes]=useState<string[]>([]);
  const toggle=(code:string)=>setCodes(v=>v.includes(code)?v.filter(c=>c!==code):v.length<5?[...v,code]:v);
  return {codes,toggle,clear:()=>setCodes([])};
}
export function CompareCheckbox({code,name,codes,toggle}:{code:string;name:string;codes:string[];toggle:(code:string)=>void}){
  return <input type="checkbox" className="v4-compare-checkbox" aria-label={"对比"+name} title={"对比"+name} checked={codes.includes(code)} disabled={codes.length>=5&&!codes.includes(code)} onChange={()=>toggle(code)}/>;
}
export function CompareToolbar({codes,clear}:{codes:string[];clear:()=>void}){
  return <div className="v4-compare-actions">{codes.length>=2?<Link className="tool-button" href={"/compare?codes="+encodeURIComponent(codes.join(","))}><Columns3 size={14}/> 对比 ({codes.length})</Link>:<button type="button" className="tool-button" disabled title="选择2至5只股票"><Columns3 size={14}/> 对比 ({codes.length})</button>}{codes.length>0&&<button type="button" className="tool-button v4-compare-clear" onClick={clear} title="清空对比选择" aria-label="清空对比选择"><X size={14}/></button>}</div>;
}
