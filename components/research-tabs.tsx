"use client";

import { useId, useRef, useState, type ReactNode } from "react";

type Tab = {id:string;label:string;content:ReactNode};
export default function ResearchTabs({tabs,label}:{tabs:Tab[];label:string}) {
  const [active,setActive]=useState(tabs[0]?.id ?? "");
  const base=useId();
  const refs=useRef<Array<HTMLButtonElement|null>>([]);
  return <div className="research-tabs">
    <div className="research-tab-list" role="tablist" aria-label={label}>
      {tabs.map((tab,i)=><button key={tab.id} ref={el=>{refs.current[i]=el;}} type="button" role="tab"
        id={base+"-"+tab.id} aria-controls={base+"-panel-"+tab.id} aria-selected={active===tab.id}
        tabIndex={active===tab.id?0:-1} className={active===tab.id?"selected":""}
        onClick={()=>setActive(tab.id)}
        onKeyDown={e=>{let n=i;if(e.key==="ArrowRight")n=(i+1)%tabs.length;else if(e.key==="ArrowLeft")n=(i+tabs.length-1)%tabs.length;else if(e.key==="Home")n=0;else if(e.key==="End")n=tabs.length-1;else return;e.preventDefault();setActive(tabs[n].id);refs.current[n]?.focus();}}>
        {tab.label}
      </button>)}
    </div>
    {tabs.map(tab=><div key={tab.id} role="tabpanel" id={base+"-panel-"+tab.id} aria-labelledby={base+"-"+tab.id}
      hidden={active!==tab.id} className={active===tab.id?"research-tab-content strategy-view-enter":"research-tab-content"}>{tab.content}</div>)}
  </div>;
}
