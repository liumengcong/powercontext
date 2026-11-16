import { pipeline, env } from '@huggingface/transformers';
import { MODEL_ID, QUERY_PREFIX, DIMENSION, type Backend, type Precision, type ModelManifest } from './shared.ts';
// Models are served as files by the local application; only this browser worker computes embeddings.
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.useBrowserCache = true;
if(env.backends.onnx.wasm) {
  env.backends.onnx.wasm.wasmPaths = '/ort/';
  // Single-thread works without cross-origin isolation and is reproducible on Chrome/Edge.
  env.backends.onnx.wasm.numThreads = 1;
}
let extractor: any, manifest: ModelManifest, backend='', precision:Precision='q8';
let loadMs=0, fallback='';
const send=(id:number,kind:string,value:unknown)=>self.postMessage({id,kind,value});
let serial=Promise.resolve();
self.onmessage=event=>{serial=serial.then(()=>handle(event.data)).catch(e=>send(event.data.id,'error',String(e)));};
async function handle({id,type,payload}:{id:number;type:string;payload:any}) {
  if(type==='init') {
    const t=performance.now();
    if(extractor)await extractor.dispose(); extractor=undefined;
    precision=payload.precision;
    const response=await fetch('/models/manifest.json');
    if(!response.ok)throw Error('模型尚未准备，请先执行 npm run models');
    manifest=await response.json();
    if(manifest.id!==MODEL_ID || manifest.dimension!==DIMENSION || !/^[a-f0-9]{40}$/.test(manifest.revision)) throw Error('模型清单不正确');
    const requested=payload.backend as Backend;
    let gpu=false;
    if(requested!=='wasm' && (self.navigator as any).gpu) {
      try{ const adapter=await (self.navigator as any).gpu.requestAdapter();gpu=!!adapter; }catch{}
    }
    fallback='';
    const load=async(device:'webgpu'|'wasm')=>{
      send(id,'progress',{stage:'load',message:`正在加载 ${precision.toUpperCase()} · ${device.toUpperCase()}`});
      const p=await pipeline('feature-extraction',MODEL_ID,{
        device,dtype:precision,revision:manifest.revision,
        progress_callback:(p:any)=>send(id,'progress',{stage:'model',...p}),
      });
      // Session creation can succeed while its first dispatch fails; warm-up is part of capability detection.
      try { await (p as any)(['模型预热'],{pooling:'cls',normalize:true}); }
      catch(e){await p.dispose();throw e;}
      return p;
    };
    if(gpu) {
      try{extractor=await load('webgpu');backend='webgpu';}
      catch(e){fallback=`WebGPU ${precision} 初始化或预热失败，已降级到 WASM：${String(e)}`;}
    } else if(requested!=='wasm') fallback='当前浏览器没有可用 WebGPU 适配器，已使用 WASM';
    if(!extractor){extractor=await load('wasm');backend='wasm';}
    loadMs=performance.now()-t;
    send(id,'done',{backend,precision,loadMs,fallback,manifest});
  } else if(type==='embed') {
    if(!extractor)throw Error('请先加载模型');
    const t=performance.now(),texts=payload.texts as string[],vectors:number[][]=[];
    const batchSize=Math.max(1,Math.min(32,Number(payload.batchSize)||8));
    // Length-sorted batches reduce padding; return vectors in original source order.
    const ordered=texts.map((text,i)=>({text:payload.query?QUERY_PREFIX+text:text,i})).sort((a,b)=>a.text.length-b.text.length);
    for(let start=0;start<ordered.length;start+=batchSize) {
      const batch=ordered.slice(start,start+batchSize);
      const result=await extractor(batch.map(x=>x.text),{pooling:'cls',normalize:true});
      const rows=result.tolist() as number[][];
      rows.forEach((v,j)=>{if(v.length!==DIMENSION||!v.every(Number.isFinite))throw Error('模型输出向量无效');vectors[batch[j].i]=v;});
      send(id,'progress',{stage:'embedding',done:Math.min(start+batchSize,texts.length),total:texts.length});
    }
    send(id,'done',{vectors,embeddingMs:performance.now()-t,backend,precision});
  } else throw Error('未知计算请求');
}
