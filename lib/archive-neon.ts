import "server-only";
import {S3Client,GetObjectCommand,ListObjectsV2Command} from "@aws-sdk/client-s3";
import {ArchiveUnavailable,validateArchiveKey,validateMaxBytes,validateListOptions,type ArchiveStorage} from "./archive-storage";

export function createNeonStorage():ArchiveStorage{
  const endpoint=process.env.NEON_STORAGE_ENDPOINT;
  const bucket=process.env.NEON_STORAGE_BUCKET;
  const accessKeyId=process.env.NEON_STORAGE_READ_ACCESS_KEY_ID;
  const secretAccessKey=process.env.NEON_STORAGE_READ_SECRET_ACCESS_KEY;
  if(!endpoint||!bucket||!accessKeyId||!secretAccessKey||process.env.NEON_STORAGE_PRIVATE_CONFIRMED!=="true")
    throw new ArchiveUnavailable("历史行情读取尚未配置");
  const url=new URL(endpoint);
  const match=/^br-[a-z0-9-]+\.storage\.c-\d+\.([a-z0-9-]+)\.aws\.neon\.tech$/.exec(url.hostname);
  if(url.protocol!=="https:"||!match||url.port||url.username||url.password||url.search||url.hash||url.pathname!=="/"||!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket))
    throw new ArchiveUnavailable("历史行情读取配置无效");
  const client=new S3Client({endpoint,region:process.env.NEON_STORAGE_REGION||match[1],forcePathStyle:true,credentials:{accessKeyId,secretAccessKey},maxAttempts:2});
  return {
    async get(key,maxBytes,timeout=12000){
      validateArchiveKey(key);validateMaxBytes(maxBytes);
      try{
        const r=await client.send(new GetObjectCommand({Bucket:bucket,Key:key}),{abortSignal:AbortSignal.timeout(timeout)});
        const body=r.Body;
        if(!body||r.ContentLength===undefined||!Number.isSafeInteger(r.ContentLength)||r.ContentLength<0||r.ContentLength>maxBytes){
          if(body&&"destroy" in body&&typeof body.destroy==="function")body.destroy();
          throw Error("Invalid archive object size");
        }
        // Node.js SDK stream: bound actual bytes, not only the advertised size.
        const chunks:Uint8Array[]=[];
        let length=0;
        try{
          for await(const chunk of body as AsyncIterable<Uint8Array>){
            length+=chunk.byteLength;
            if(length>r.ContentLength||length>maxBytes)throw Error("Invalid archive object size");
            chunks.push(chunk);
          }
        }finally{
          if("destroy" in body&&typeof body.destroy==="function")body.destroy();
        }
        if(length!==r.ContentLength)throw Error("Truncated archive object");
        return Buffer.concat(chunks,length);
      }catch(e){
        const x=e as {name?:string;$metadata?:{httpStatusCode?:number}};
        if(x.name==="NoSuchKey"||x.$metadata?.httpStatusCode===404)return null;
        throw e;
      }
    },
    async list(prefix,options){
      validateArchiveKey(prefix,true);validateListOptions(options);
      const r=await client.send(new ListObjectsV2Command({Bucket:bucket,Prefix:prefix,Delimiter:options.delimiter,MaxKeys:options.maxKeys}),{abortSignal:AbortSignal.timeout(options.timeout??12000)});
      return {keys:(r.Contents??[]).map(x=>x.Key??"").filter(Boolean),prefixes:(r.CommonPrefixes??[]).map(x=>x.Prefix??"").filter(Boolean),truncated:!!r.IsTruncated};
    },
    close(){client.destroy();},
  };
}
