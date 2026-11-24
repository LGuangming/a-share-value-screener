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

export type Adjustment="unadjusted"|"forward"|"backward";
export type Factor={day:string;factor:number};
export function mergeArchivedBars(years:MarketBar[][]):MarketBar[]{
  const sorted=years.flat().sort((a,b)=>a.day.localeCompare(b.day));
  if(new Set(sorted.map(b=>b.day)).size!==sorted.length||sorted.some(b=>!completeBar(b)))throw Error("Invalid archived series");
  return sorted;
}
// Anchor to the displayed interval, not today's unverified factor.
export function adjustBars(bars:MarketBar[],factors:Factor[],mode:Adjustment):MarketBar[]{
  if(mode==="unadjusted"||!bars.length)return bars;
  const byDay=new Map(factors.map(f=>[f.day,f.factor]));
  if(byDay.size!==factors.length)throw Error("Duplicate adjustment factor");
  const required=bars.map(b=>byDay.get(b.day));
  if(required.some(f=>typeof f!=="number"||!Number.isFinite(f)||f<=0))throw Error("复权因子覆盖不完整");
  const anchor=required[mode==="forward"?required.length-1:0]!;
  return bars.map((b,i)=>{
    const ratio=required[i]!/anchor;
    const out={...b,close:b.close*ratio};
    if(b.open!==undefined)out.open=b.open*ratio;
    if(b.high!==undefined)out.high=b.high*ratio;
    if(b.low!==undefined)out.low=b.low*ratio;
    if(!completeBar(out))throw Error("Invalid adjusted bar");
    return out;
  });
}
type IndicatorPoint={time:string;value:number};
function ema(values:number[],period:number){
  let prev=values[0]??0;
  return values.map((v,i)=>prev=i===0?v:prev+(2/(period+1))*(v-prev));
}
export function macd(bars:MarketBar[],fast=12,slow=26,signal=9){
  const prices=bars.map(b=>b.close),a=ema(prices,fast),b=ema(prices,slow);
  const dif=a.map((v,i)=>v-b[i]),dea=ema(dif,signal),start=slow+signal-2;
  return bars.slice(start).map((bar,j)=>{const i=j+start;return {time:bar.day,dif:dif[i],dea:dea[i],histogram:2*(dif[i]-dea[i])};});
}
export function rsi(bars:MarketBar[],period=14):IndicatorPoint[]{
  if(bars.length<=period)return [];
  let gain=0,loss=0;const out:IndicatorPoint[]=[];
  for(let i=1;i<bars.length;i++){
    const delta=bars[i].close-bars[i-1].close,g=Math.max(0,delta),l=Math.max(0,-delta);
    if(i<=period){gain+=g/period;loss+=l/period;}
    else{gain=(gain*(period-1)+g)/period;loss=(loss*(period-1)+l)/period;}
    if(i>=period)out.push({time:bars[i].day,value:loss===0?(gain===0?50:100):100-100/(1+gain/loss)});
  }
  return out;
}
export function boll(bars:MarketBar[],period=20,multiplier=2){
  return bars.flatMap((b,i)=>{
    if(i<period-1)return [];
    const window=bars.slice(i-period+1,i+1).map(x=>x.close),middle=window.reduce((s,v)=>s+v,0)/period;
    const std=Math.sqrt(window.reduce((s,v)=>s+(v-middle)**2,0)/period);
    return [{time:b.day,middle,upper:middle+multiplier*std,lower:middle-multiplier*std}];
  });
}
export function kdj(bars:MarketBar[],period=9){
  if(!bars.every(completeBar))return [];
  let k=50,d=50;
  return bars.flatMap((b,i)=>{
    if(i<period-1)return [];
    const w=bars.slice(i-period+1,i+1),high=Math.max(...w.map(x=>x.high!)),low=Math.min(...w.map(x=>x.low!));
    const rsv=high===low?50:(b.close-low)/(high-low)*100;
    k=(2*k+rsv)/3;d=(2*d+k)/3;
    return [{time:b.day,k,d,j:3*k-2*d}];
  });
}


// Recent means the last 120 verified MARKET sessions, not 120 individual
// stock observations. Suspensions and newly listed securities keep their
// genuine gaps; never reach farther back just to fill a particular stock.
export const RECENT_SESSIONS=120;
export function recentWindow(bars:MarketBar[],sessions:string[],limit=RECENT_SESSIONS){
  if(!Number.isInteger(limit)||limit<=0)throw Error("Invalid recent session limit");
  const dates=[...new Set(sessions)].sort().slice(-limit);
  const allowed=new Set(dates);
  return {dates,bars:mergeArchivedBars([bars]).filter(b=>allowed.has(b.day)),from:dates[0]??"",to:dates.at(-1)??""};
}
export function recentMonthRange(from:string,to:string):{year:number;month:string}[]{
  if(!/^20\d{2}-\d{2}-\d{2}$/.test(from)||!/^20\d{2}-\d{2}-\d{2}$/.test(to))throw Error("Invalid recent range");
  const a=new Date(from+"T00:00:00Z"),b=new Date(to+"T00:00:00Z");
  if(!Number.isFinite(+a)||!Number.isFinite(+b)||a.toISOString().slice(0,10)!==from||b.toISOString().slice(0,10)!==to||from<"2020-01-01"||from>to||(+b-+a)>550*86400000)throw Error("Invalid recent range");
  const result:{year:number;month:string}[]=[];
  for(const d=new Date(Date.UTC(a.getUTCFullYear(),a.getUTCMonth(),1));d<=b;d.setUTCMonth(d.getUTCMonth()+1)){
    result.push({year:d.getUTCFullYear(),month:String(d.getUTCMonth()+1).padStart(2,"0")});
  }
  return result;
}
