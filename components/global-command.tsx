"use client";
import {useEffect,useRef,useState} from "react";
import {useRouter} from "next/navigation";
import {Search,ArrowUpRight,X} from "lucide-react";
import {useStockSearch} from "@/components/research-data";
import {strategies} from "@/lib/strategies";

const pages=[{name:"选股中心",href:"/"},{name:"行情研究",href:"/research"},{name:"策略汇总",href:"/?strategy=summary"},{name:"股票对比",href:"/compare"},{name:"历史候选变化",href:"/candidate-history"},{name:"数据中心",href:"/data-center"},{name:"策略中心",href:"/strategies"},{name:"交易观察",href:"/signals"},{name:"策略回测",href:"/backtest"},{name:"筛选方法",href:"/methodology"}];
export default function GlobalCommand(){
  const dialog=useRef<HTMLDialogElement>(null);
  const input=useRef<HTMLInputElement>(null);
  const [open,setOpen]=useState(false),[q,setQ]=useState(""),[index,setIndex]=useState(0);
  const router=useRouter();
  const result=useStockSearch(q,open);
  const needle=q.trim().toLowerCase();
  const options=[...pages.filter(p=>!needle||p.name.toLowerCase().includes(needle)),...strategies.filter(s=>needle&&s.name.toLowerCase().includes(needle)).map(s=>({name:s.name,href:"/strategies/"+encodeURIComponent(s.id)})),...result.stocks.map(s=>({name:s.name+" · "+s.ts_code,href:"/research?code="+encodeURIComponent(s.ts_code)}))];
  function show(){if(!dialog.current?.open){setQ("");setIndex(0);setOpen(true);dialog.current?.showModal();input.current?.focus();}}
  function go(href:string){dialog.current?.close();router.push(href);}
  useEffect(()=>{const key=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();if(dialog.current?.open)dialog.current.close();else show();}};window.addEventListener("keydown",key);return()=>window.removeEventListener("keydown",key);},[]);
  return <><button type="button" className="global-command-button" title="全局搜索 (Ctrl+K)" aria-label="打开全局搜索" onClick={show}><Search size={16}/><span>搜索股票、策略或页面</span><kbd>Ctrl K</kbd></button>
  <dialog ref={dialog} className="global-command-dialog" aria-label="全局搜索" onClose={()=>setOpen(false)} onClick={e=>{if(e.target===dialog.current)dialog.current?.close();}}>
  <div className="command-inner"><div className="command-input"><Search size={19}/><input ref={input} aria-label="搜索股票、策略或页面" placeholder="输入股票名称、代码或页面" maxLength={40} value={q} onChange={e=>{setQ(e.target.value);setIndex(0);}} onKeyDown={e=>{if(e.key==="ArrowDown"){e.preventDefault();setIndex(i=>Math.min(i+1,Math.max(0,options.length-1)));}else if(e.key==="ArrowUp"){e.preventDefault();setIndex(i=>Math.max(0,i-1));}else if(e.key==="Enter"&&options[index]){e.preventDefault();go(options[index].href);}}}/><button type="button" aria-label="关闭搜索" onClick={()=>dialog.current?.close()}><X size={17}/></button></div><div className="command-results">{options.map((p,i)=><button key={p.href} type="button" className={i===index?"command-selected":""} onMouseEnter={()=>setIndex(i)} onClick={()=>go(p.href)}>{p.name}<ArrowUpRight size={14}/></button>)}{result.loading&&<p>正在搜索股票…</p>}{result.error&&<p>{result.error}</p>}{!options.length&&!result.loading&&!result.error&&<p>暂无匹配结果</p>}</div><div className="command-footer">↑ ↓ 选择 · Enter 打开 · Esc 关闭</div></div>
  </dialog></>;
}
