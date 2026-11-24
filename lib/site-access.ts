import "server-only";
import {createHash,createHmac,randomBytes,timingSafeEqual} from "node:crypto";

export const SESSION_SECONDS=7*24*60*60;
export const COOKIE_NAME=process.env.NODE_ENV==="production"?"__Host-value-radar-access":"value-radar-access";
const digest=(value:string)=>createHash("sha256").update(value,"utf8").digest();
export function accessConfig(){
  const password=process.env.SITE_ACCESS_PASSWORD,secret=process.env.SITE_SESSION_SECRET;
  if(!password||password.length<16||password.length>512||!secret||!/^[a-fA-F0-9]{64}$/.test(secret))return null;
  return {password,secret};
}
type Config=NonNullable<ReturnType<typeof accessConfig>>;
export function passwordMatches(candidate:string,config:Config){
  return candidate.length<=512&&timingSafeEqual(digest(candidate),digest(config.password));
}
// Bind sessions to BOTH secrets; changing either invalidates every existing session.
function signature(payload:string,config:Config){
  return createHmac("sha256",Buffer.from(config.secret,"hex")).update("value-radar-session-v1\0").update(digest(config.password)).update(payload).digest("hex");
}
export function issueSession(config:Config,now=Math.floor(Date.now()/1000)){
  const payload=(now+SESSION_SECONDS)+"."+randomBytes(24).toString("hex");
  return payload+"."+signature(payload,config);
}
export function validSession(token:string|undefined,config:Config|null,now=Math.floor(Date.now()/1000)){
  if(!config||!token||!/^[0-9]{1,12}\.[a-f0-9]{48}\.[a-f0-9]{64}$/.test(token))return false;
  const [exp,nonce,sig]=token.split("."),expiry=Number(exp);
  if(!Number.isSafeInteger(expiry)||expiry<=now||expiry>now+SESSION_SECONDS)return false;
  return timingSafeEqual(Buffer.from(sig,"hex"),Buffer.from(signature(exp+"."+nonce,config),"hex"));
}
export function sessionCookie(value:string,maxAge=SESSION_SECONDS){
  return {name:COOKIE_NAME,value,httpOnly:true,secure:process.env.NODE_ENV==="production",sameSite:"lax" as const,path:"/",maxAge};
}
export function safeReturnPath(value:string|null|undefined){
  if(!value||value.length>1500||!value.startsWith("/")||value.startsWith("//")||/[\\\u0000-\u001f\u007f]/.test(value))return "/";
  try{
    const decoded=decodeURIComponent(value.split("?")[0]);
    if(decoded.startsWith("//")||decoded.includes("\\")||/[\u0000-\u001f\u007f]/.test(decoded))return "/";
    const u=new URL(value,"https://internal.invalid");
    if(u.origin!=="https://internal.invalid"||/^\/(?:login|api|_next)(?:\/|$)/i.test(decodeURIComponent(u.pathname)))return "/";
    return u.pathname+u.search;
  }catch{return "/";}
}
export function sameOriginPost(request:Request){
  const origin=request.headers.get("origin");
  if(!origin)return false;
  try{
    const u=new URL(origin),host=request.headers.get("host")??new URL(request.url).host;
    if(u.host!==host||u.username||u.password||u.pathname!=="/"||u.search||u.hash)return false;
    return u.protocol==="https:"||(u.protocol==="http:"&&(process.env.NODE_ENV!=="production"||u.hostname==="localhost"||u.hostname==="127.0.0.1"));
  }catch{return false;}
}
export const PRIVATE_HEADERS={"Cache-Control":"private, no-store, max-age=0","Pragma":"no-cache","X-Robots-Tag":"noindex, nofollow, noarchive","Referrer-Policy":"no-referrer"};
