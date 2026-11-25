import "server-only";
import {createHash} from "node:crypto";
import {unstable_cache} from "next/cache";
import {RAW_PREFIX} from "./archive-paths";
import {createLocalStorage} from "./archive-local";
import {createNeonStorage} from "./archive-neon";

export type ArchiveDriver="neon"|"local";
export type ListOptions={delimiter?:"/";maxKeys:number;timeout?:number};
export type ListResult={keys:string[];prefixes:string[];truncated:boolean};
export interface ArchiveStorage {
  get(key:string,maxBytes:number,timeout?:number):Promise<Uint8Array|null>;
  list(prefix:string,options:ListOptions):Promise<ListResult>;
  close():void;
}
export class ArchiveUnavailable extends Error {}

export function resolveArchiveDriver(value:string|undefined):ArchiveDriver{
  if(value===undefined||value===""||value==="neon")return "neon";
  if(value==="local")return value;
  throw new ArchiveUnavailable("历史归档读取配置无效");
}

// Canonical keys only: never let a manifest turn into an arbitrary file path.
export function validateArchiveKey(key:string,prefix=false){
  const value=prefix&&key.endsWith("/")?key.slice(0,-1):key;
  if(!value.startsWith(RAW_PREFIX+"/")&&!(prefix&&value===RAW_PREFIX))throw Error("Invalid archive key");
  if(/[\\\u0000-\u001f\u007f]/.test(value)||value.split("/").some(p=>!p||p==="."||p===".."))throw Error("Invalid archive key");
}
export function validateMaxBytes(max:number){
  if(!Number.isSafeInteger(max)||max<0||max>32*1024*1024)throw Error("Invalid archive size limit");
}
export function validateListOptions(options:ListOptions){
  if(!Number.isSafeInteger(options.maxKeys)||options.maxKeys<1||options.maxKeys>1000||
      (options.delimiter!==undefined&&options.delimiter!=="/"))throw Error("Invalid archive listing options");
}
export function storage():ArchiveStorage{
  const mode=resolveArchiveDriver(process.env.ARCHIVE_STORAGE_DRIVER);
  if(mode==="local")return createLocalStorage(process.env.ARCHIVE_LOCAL_ROOT);
  return createNeonStorage();
}
export async function requiredObject(s:ArchiveStorage,key:string,max:number,timeout?:number){
  const bytes=await s.get(key,max,timeout);
  if(!bytes||!bytes.length)throw Error("Missing or empty archive object");
  return bytes;
}
export function archiveCacheScope(){
  const mode=resolveArchiveDriver(process.env.ARCHIVE_STORAGE_DRIVER);
  const identity=mode==="local"?process.env.ARCHIVE_LOCAL_ROOT??"":
    JSON.stringify([process.env.NEON_STORAGE_ENDPOINT??"",process.env.NEON_STORAGE_BUCKET??""]);
  return mode+":"+createHash("sha256").update(identity).digest("hex");
}
// Scope is evaluated at request time, not at image build time. No credentials
// or filesystem paths are present in the cache key. Keep existing TTLs.
export function archiveCache<T extends unknown[],R>(fn:(...args:T)=>Promise<R>,key:string,revalidate:number){
  return (...args:T):Promise<R>=>unstable_cache(fn,[key,archiveCacheScope()],{revalidate})(...args);
}
