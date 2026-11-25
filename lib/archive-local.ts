import "server-only";
import {constants} from "node:fs";
import {lstat,realpath,open,opendir} from "node:fs/promises";
import path from "node:path";
import {ArchiveUnavailable,validateArchiveKey,validateMaxBytes,validateListOptions,type ArchiveStorage,type ListResult} from "./archive-storage";

const missing=(e:unknown)=>(e as NodeJS.ErrnoException)?.code==="ENOENT";
const MAX_VISITED=100_000;

// The mount is operator-controlled, read-only in the web container. Reject
// symlinks at every component and use O_NOFOLLOW at file open. The archive
// writer must publish by atomic rename and never replace parent directories
// with symlinks; this is not a sandbox against a malicious privileged writer.
export function createLocalStorage(configured:string|undefined):ArchiveStorage{
  if(!configured||!path.isAbsolute(configured)||path.resolve(configured)===path.parse(configured).root)
    throw new ArchiveUnavailable("本地归档读取尚未配置");
  const root=path.resolve(configured);
  async function checkedRoot(){
    try{
      const st=await lstat(root);
      if(!st.isDirectory()||st.isSymbolicLink()||await realpath(root)!==root)
        throw Error("Unsafe archive root");
    }catch{throw new ArchiveUnavailable("本地归档目录不可用");}
  }
  async function locate(key:string,kind:"file"|"directory"):Promise<string|null>{
    await checkedRoot();
    let cursor=root;
    const segments=key?key.split("/"):[];
    try{
      for(let i=0;i<segments.length;i++){
        cursor=path.join(cursor,segments[i]);
        const st=await lstat(cursor);
        if(st.isSymbolicLink())throw Error("Archive symlink rejected");
        if(i<segments.length-1||kind==="directory"){
          if(!st.isDirectory())throw Error("Invalid archive directory");
        }else if(!st.isFile())throw Error("Invalid archive file");
      }
      return cursor;
    }catch(e){if(missing(e))return null;throw e;}
  }
  return {
    async get(key,maxBytes){
      validateArchiveKey(key);validateMaxBytes(maxBytes);
      const file=await locate(key,"file");
      if(file===null)return null;
      // O_NONBLOCK avoids hanging if a special file replaces the entry.
      const handle=await open(file,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
      try{
        const before=await handle.stat();
        if(!before.isFile()||before.size>maxBytes)throw Error("Invalid archive object size");
        const bytes=Buffer.alloc(before.size);
        let offset=0;
        while(offset<bytes.length){
          const {bytesRead}=await handle.read(bytes,offset,bytes.length-offset,offset);
          if(!bytesRead)throw Error("Truncated archive object");
          offset+=bytesRead;
        }
        const after=await handle.stat();
        if(after.size!==before.size||after.mtimeMs!==before.mtimeMs)throw Error("Archive object changed during read");
        return bytes;
      }finally{await handle.close();}
    },
    async list(prefix,options):Promise<ListResult>{
      validateArchiveKey(prefix,true);validateListOptions(options);
      const split=prefix.lastIndexOf("/");
      const base=prefix.slice(0,split),tail=prefix.slice(split+1);
      const directory=await locate(base,"directory");
      const keys:string[]=[],prefixes:string[]=[];
      let truncated=false,visited=0;
      if(directory===null)return {keys,prefixes,truncated};
      function visit(){
        if(++visited>MAX_VISITED)throw Error("Archive listing scan limit exceeded");
      }
      function add(target:string[],value:string){
        if(keys.length+prefixes.length>=options.maxKeys){truncated=true;return;}
        target.push(value);
      }
      async function containsFile(dir:string):Promise<boolean>{
        for await(const entry of await opendir(dir)){
          visit();
          if(entry.isSymbolicLink())throw Error("Archive symlink rejected");
          if(entry.isFile())return true;
          if(entry.isDirectory()){
            if(await containsFile(path.join(dir,entry.name)))return true;
          }else throw Error("Invalid archive entry");
        }
        return false;
      }
      async function walk(dir:string,key:string,filter:string){
        for await(const entry of await opendir(dir)){
          visit();
          if(!entry.name.startsWith(filter))continue;
          if(entry.isSymbolicLink())throw Error("Archive symlink rejected");
          const child=path.join(dir,entry.name),childKey=key+"/"+entry.name;
          validateArchiveKey(childKey);
          if(entry.isDirectory()){
            if(options.delimiter){
              if(await containsFile(child))add(prefixes,childKey+"/");
            }else await walk(child,childKey,"");
          }else if(entry.isFile())add(keys,childKey);
          else throw Error("Invalid archive entry");
          if(truncated)return;
        }
      }
      await walk(directory,base,tail);
      return {keys:keys.sort(),prefixes:prefixes.sort(),truncated};
    },
    close(){},
  };
}
