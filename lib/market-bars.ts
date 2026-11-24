export type MarketBar={day:string;close:number;open?:number;high?:number;low?:number;volume?:number;amount?:number};
export type TradeMark={day:string;side:"buy"|"sell";label:string};
export function completeBar(b:MarketBar): b is MarketBar & {open:number;high:number;low:number} {
  return typeof b.open==="number"&&typeof b.high==="number"&&typeof b.low==="number"&&
    [b.open,b.high,b.low,b.close].every(x=>Number.isFinite(x)&&x>0)&&
    b.high>=Math.max(b.open,b.close)&&b.low<=Math.min(b.open,b.close);
}
export function movingAverage(bars:MarketBar[],period:number) {
  let sum=0;
  return bars.flatMap((b,i)=>{sum+=b.close;if(i>=period)sum-=bars[i-period].close;
    return i>=period-1?[{time:b.day,value:sum/period}]:[];});
}
export function aggregateBars(bars:MarketBar[],period:"D"|"W"|"M"):MarketBar[] {
  if(period==="D")return bars;
  const groups=new Map<string,MarketBar[]>();
  for(const b of bars){
    const day=new Date(b.day+"T00:00:00Z");
    if(period==="W")day.setUTCDate(day.getUTCDate()-((day.getUTCDay()+6)%7));
    const key=period==="M"?b.day.slice(0,7):day.toISOString().slice(0,10);
    const g=groups.get(key);if(g)g.push(b);else groups.set(key,[b]);
  }
  return [...groups.values()].map(g=>{
    const out:MarketBar={day:g[g.length-1].day,close:g[g.length-1].close};
    if(g.every(completeBar)){out.open=g[0].open;out.high=Math.max(...g.map(b=>b.high));out.low=Math.min(...g.map(b=>b.low));}
    if(g.every(b=>typeof b.volume==="number"))out.volume=g.reduce((s,b)=>s+b.volume!,0);
    if(g.every(b=>typeof b.amount==="number"))out.amount=g.reduce((s,b)=>s+b.amount!,0);
    return out;
  });
}
