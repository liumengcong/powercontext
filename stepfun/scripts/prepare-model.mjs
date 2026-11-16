import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve, dirname } from 'node:path';
const id='Xenova/bge-small-zh-v1.5';
let endpoint=(process.env.MODEL_ENDPOINT || 'https://huggingface.co').replace(/\/$/,'');
const root=resolve('public/models');
const lockPath=resolve('model.lock.json');
let old;
try { old=JSON.parse(await readFile(lockPath,'utf8')); } catch {}
// Reuse verified local files without requiring a network connection.
if(old?.revision && old?.files?.length) {
  const matches=await Promise.all(old.files.map(async f=>{try{return createHash('sha256').update(await readFile(resolve(root,id,f.path))).digest('hex')===f.sha256;}catch{return false;}}));
  if(matches.every(Boolean)){await writeFile(resolve(root,'manifest.json'),JSON.stringify(old,null,2)+'\n');console.log('本地模型文件已校验，无需下载：'+old.revision);process.exit(0);}
}
async function get(url) {
  for(let attempt=0;attempt<3;attempt++) {
    try {const r=await fetch(url,{signal:AbortSignal.timeout(url.includes('/api/models/')?15000:180000)});if(!r.ok)throw Error(`HTTP ${r.status}: ${url}`);return r;}
    catch(e){if(attempt===2)throw e;await new Promise(r=>setTimeout(r,Math.min(15000,3000*(attempt+1))));console.log('网络重试',attempt+1);}
  }
}
// First download resolves the repository's main branch to an immutable commit.
// All future installs use that commit; committed locks keep reviewer/user identical.
const revision=process.env.MODEL_REVISION || old?.revision;
let info;
try {info=await (await get(`${endpoint}/api/models/${id}${revision?`/revision/${revision}`:''}`)).json();} catch(e) {
  if(process.env.MODEL_ENDPOINT)throw e;
  endpoint='https://hf-mirror.com';console.log('原始下载站不可用，改用镜像；模型内容仍校验 SHA256');
  info=await (await get(`${endpoint}/api/models/${id}${revision?`/revision/${revision}`:''}`)).json();
}
if(!/^[a-f0-9]{40}$/.test(info.sha))throw Error('未获取到模型 commit SHA');
const files=['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','vocab.txt','onnx/model.onnx','onnx/model_quantized.onnx'];
const available=new Set(info.siblings.map(x=>x.rfilename));
for(const f of files) if(!available.has(f))throw Error(`模型仓库缺少 ${f}`);
const downloadStart=performance.now();
const manifest={id,revision:info.sha,dimension:512,pooling:'cls',normalize:true,source:`https://huggingface.co/${id}`,endpoint,files:[],modelBytes:{fp32:0,q8:0}};
for(const file of files) {
  const local=resolve(root,id,file); await mkdir(dirname(local),{recursive:true});
  let data; try{data=await readFile(local);}catch{}
  const prior=old?.files?.find(x=>x.path===file);
  const valid=old?.revision===info.sha && prior && data && createHash('sha256').update(data).digest('hex')===prior.sha256;
  if(!valid) {
    console.log('下载',file);
    data=Buffer.from(await (await get(`${endpoint}/${id}/resolve/${info.sha}/${file}`)).arrayBuffer());
    const upstream=info.siblings.find(x=>x.rfilename===file);
    if(upstream?.size && data.length!==upstream.size)throw Error(`文件大小不符：${file}`);
    if(upstream?.lfs?.sha256 && createHash('sha256').update(data).digest('hex')!==upstream.lfs.sha256)throw Error(`SHA256 不符：${file}`);
    await writeFile(local,data);
  }
  manifest.files.push({path:file,bytes:data.length,sha256:createHash('sha256').update(data).digest('hex')});
  if(file==='onnx/model.onnx')manifest.modelBytes.fp32=data.length;
  if(file==='onnx/model_quantized.onnx')manifest.modelBytes.q8=data.length;
}
const config=JSON.parse(await readFile(resolve(root,id,'config.json'),'utf8'));
if(config.hidden_size!==512)throw Error(`模型维度 ${config.hidden_size} 与应用配置不一致`);
manifest.preparationMs=performance.now()-downloadStart;
manifest.preparedAt=new Date().toISOString();
await writeFile(lockPath,JSON.stringify(manifest,null,2)+'\n');
await writeFile(resolve(root,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
console.log(`模型已锁定 ${info.sha}\nONNX FP32=${manifest.modelBytes.fp32} 字节，INT8=${manifest.modelBytes.q8} 字节，压缩比=${(manifest.modelBytes.fp32/manifest.modelBytes.q8).toFixed(3)}`);
