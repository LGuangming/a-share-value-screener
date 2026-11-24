"use client";
import {useEffect,useRef,type RefObject} from "react";

type DismissReason="outside"|"escape";
/** Treat both a trigger and its portaled panel as one interactive region. */
export function useDismissOnOutside(anchor:RefObject<HTMLElement|null>,onDismiss:(reason:DismissReason)=>void,panel?:RefObject<HTMLElement|null>){
  const dismiss=useRef(onDismiss);
  useEffect(()=>{dismiss.current=onDismiss;},[onDismiss]);
  useEffect(()=>{
    function inside(event:Event){
      const path=event.composedPath();
      return [anchor.current,panel?.current].some(node=>node!==null&&node!==undefined&&(path.includes(node)||(event.target instanceof Node&&node.contains(event.target))));
    }
    function outside(event:Event){if(!inside(event))dismiss.current("outside");}
    function key(event:KeyboardEvent){if(event.key==="Escape"&&inside(event))dismiss.current("escape");}
    document.addEventListener("pointerdown",outside,true);
    document.addEventListener("focusin",outside,true);
    document.addEventListener("keydown",key);
    return ()=>{
      document.removeEventListener("pointerdown",outside,true);
      document.removeEventListener("focusin",outside,true);
      document.removeEventListener("keydown",key);
    };
  },[anchor,panel]);
}
