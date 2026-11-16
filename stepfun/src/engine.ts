import { hash, MODEL_ID, QUERY_PREFIX, type Backend, type Precision, type ModelManifest } from './shared.ts';
export type Info={backend:string;precision:Precision;loadMs:number;fallback:string;manifest:ModelManifest};
export class Engine {
  private worker=new Worker(new URL('./embedding.worker.ts',import.meta.url),{type:'module'});
  private seq=0;
  private jobs=new Map<number,{resolve:(x:any)=>void;reject:(e:Error)=>void;progress?:(x:any)=>void}>();
  info!:Info;
  profile='';
  constructor(){
    this.worker.onmessage=({data:d})=>{const j=this.jobs.get(d.id);if(!j)return;
      if(d.kind==='progress')j.progress?.(d.value);
      else {this.jobs.delete(d.id);d.kind==='error'?j.reject(new Error(d.value)):j.resolve(d.value);}
    };
    this.worker.onerror=e=>{for(const j of this.jobs.values())j.reject(new Error(e.message));this.jobs.clear();};
  }
  private call(type:string,payload:unknown,progress?:(x:any)=>void):Promise<any>{
    const id=++this.seq;return new Promise((resolve,reject)=>{this.jobs.set(id,{resolve,reject,progress});this.worker.postMessage({id,type,payload});});
  }
  async init(precision:Precision='q8',backend:Backend='auto',progress?:(x:any)=>void){
    this.info=await this.call('init',{precision,backend},progress);
    this.profile=await hash(JSON.stringify({model:MODEL_ID,revision:this.info.manifest.revision,precision,pooling:'cls',normalize:true,query:QUERY_PREFIX,chunk:240,overlap:40}));
    return this.info;
  }
  async embed(texts:string[],query=false,batchSize=8,progress?:(x:any)=>void):Promise<{vectors:number[][];embeddingMs:number;backend:string}>{
    return this.call('embed',{texts,query,batchSize},progress);
  }
  dispose(){this.worker.terminate();for(const j of this.jobs.values())j.reject(new Error('计算已终止'));this.jobs.clear();}
}
export async function api(path:string,body?:unknown):Promise<any>{
  const response=await fetch('/api/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();if(!response.ok)throw Error(value.error || `请求失败 ${response.status}`);return value;
}
export function saveFile(name:string,content:string,type='application/json') {
  const url=URL.createObjectURL(new Blob([content],{type})),a=document.createElement('a');
  a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
