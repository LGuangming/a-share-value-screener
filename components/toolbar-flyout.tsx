"use client";
import {useLayoutEffect,useState,type ReactNode,type RefObject} from "react";
import {createPortal} from "react-dom";

/** Keep dropdowns outside the single-row horizontally scrollable toolbar. */
export default function ToolbarFlyout({anchor,children,align="left",width,placement="auto"}:{anchor:RefObject<HTMLDivElement|null>;children:ReactNode;align?:"left"|"right";width:number;placement?:"auto"|"below"}){
  const [pos,setPos]=useState<{left:number;top:number;width:number;maxHeight:number}|null>(null);
  useLayoutEffect(()=>{
    function update(){
      if(!anchor.current)return;
      const rect=anchor.current.getBoundingClientRect();
      const toolbar=(anchor.current.closest(".filter-form")??anchor.current.closest(".filters"))?.getBoundingClientRect();
      if(toolbar&&(rect.right<=toolbar.left||rect.left>=toolbar.right)){setPos(null);return;}
      const w=Math.min(width,window.innerWidth-16);
      const left=Math.max(8,Math.min(window.innerWidth-w-8,align==="right"?rect.right-w:rect.left));
      const below=window.innerHeight-rect.bottom-12;
      const above=rect.top-12;
      const flip=placement==="auto"&&below<180&&above>below;
      const maxHeight=Math.max(0,Math.min(360,flip?above:below));
      if(maxHeight<24){setPos(null);return;}
      setPos({left,top:flip?Math.max(8,rect.top-maxHeight-6):rect.bottom+6,width:w,maxHeight});
    }
    update();window.addEventListener("resize",update);window.addEventListener("scroll",update,true);
    return ()=>{window.removeEventListener("resize",update);window.removeEventListener("scroll",update,true);};
  },[anchor,align,width,placement]);
  return pos?createPortal(<div className="toolbar-flyout" style={pos}>{children}</div>,document.body):null;
}
