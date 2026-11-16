import {Engine,api,type Info} from './engine.ts';
import {quality,hash,median,type Precision,type Backend} from './shared.ts';
export interface Sample {id:string;text:string;source:string;}
interface Dataset{documents:Sample[];queries:{text:string;relevant:Record<string,number>}[];version:string;}
const configs:{name:string;precision:Precision;backend:Backend;batch:number}[]=[
  {name:'fp32_wasm_b1',precision:'fp32',backend:'wasm',batch:1},
  {name:'q8_wasm_b1',precision:'q8',backend:'wasm',batch:1},
  {name:'q8_wasm_b8',precision:'q8',backend:'wasm',batch:8},
  {name:'q8_webgpu_b8',precision:'q8',backend:'webgpu',batch:8},
];
export async function benchmark(progress:(s:string)=>void,rounds=3){
  const dataset:Dataset=await api('eval-data');
  const report:any={schema:1,createdAt:new Date().toISOString(),device:{userAgent:navigator.userAgent,hardwareConcurrency:navigator.hardwareConcurrency,deviceMemory:(navigator as any).deviceMemory??null},
    dataset:{version:dataset.version,sha256:await hash(JSON.stringify(dataset)),documents:dataset.documents.length,queries:dataset.queries.length},
    conditions:{rounds,k:5,candidateWindow:50,rrfConstant:60,queryCache:false,order:'固定顺序，全部原始样本保留',timing:'load 含本地文件读取/缓存、session 创建及首次预热；不含准备模型的下载。e2e 计至评测结果文本进入双 requestAnimationFrame；交互卡片耗时见操作记录' },runs:[]};
  for(const config of configs){
    for(let round=0;round<rounds;round++){
      const engine=new Engine(),run:any={...config,round,queries:[]};
      try {
        progress(`${config.name} · 第 ${round+1}/${rounds} 轮：加载模型`);
        const info:Info=await engine.init(config.precision,config.backend);
        run.actualBackend=info.backend;run.fallback=info.fallback;run.loadMs=info.loadMs;
        run.revision=info.manifest.revision;run.modelBytes=info.manifest.modelBytes[config.precision];
        run.profile=engine.profile;
        // Separate immutable precision profiles; identical corpus and relevance across all conditions.
        const kb='eval_v1';
        const embeddings=await engine.embed(dataset.documents.map(d=>d.text),false,config.batch);
        run.documentEmbeddingMs=embeddings.embeddingMs;
        run.documentsPerSecond=dataset.documents.length*1000/embeddings.embeddingMs;
        const write=await api('import',{kb,profile:engine.profile,docs:dataset.documents.map((d,i)=>({id:d.id,sourceId:d.id,source:d.source,text:d.text,chunk:0,vector:embeddings.vectors[i]})),refreshEachBatch:false});
        Object.assign(run,{writeMs:write.writeMs,refreshMs:write.refreshMs,count:write.count});
        const status=await api('status',{kb,profile:engine.profile});report.seekdb={version:status.version,dimension:512,distance:'cosine',fulltext:'ngram(2)',mode:(await api('health')).mode};
        // Warm all model execution paths; warm-up timings are stored separately, not silently dropped.
        const warm=await engine.embed(['数据库混合检索'],true,1);run.queryWarmupMs=warm.embeddingMs;
        for(const q of dataset.queries){
          const t=performance.now();
          const emb=await engine.embed([q.text],true,1);
          const t2=performance.now();
          const result=await api('search',{kb,profile:engine.profile,text:q.text,vector:emb.vectors[0],k:50,mode:'hybrid'});
          const apiRoundTripMs=performance.now()-t2;
          const ranking=result.hits.map((h:any)=>String(h.metadata.sourceId));
          // Render the benchmark result text before measuring display latency.
          const preview=document.querySelector('#eval-preview');
          if(preview)preview.textContent=result.hits.slice(0,5).map((h:any)=>h.text).join('\n');
          await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
          run.queries.push({query:q.text,embeddingMs:emb.embeddingMs,seekdbMs:result.seekdbMs,apiRoundTripMs,e2eMs:performance.now()-t,
            ranking,metrics:quality(ranking,q.relevant,5)});
          progress(`${config.name} · 第 ${round+1} 轮 · ${run.queries.length}/${dataset.queries.length} 条查询`);
        }
        run.summary={recall5:mean(run.queries.map((q:any)=>q.metrics.recall)),ndcg5:mean(run.queries.map((q:any)=>q.metrics.ndcg)),
          queryEmbeddingMedianMs:median(run.queries.map((q:any)=>q.embeddingMs)),seekdbMedianMs:median(run.queries.map((q:any)=>q.seekdbMs)),e2eMedianMs:median(run.queries.map((q:any)=>q.e2eMs))};
        run.completed=true;
      }catch(e){run.completed=false;run.error=String(e);progress(`${config.name} 失败：${e}`);}
      finally{engine.dispose();report.runs.push(run);}
    }
  }
  report.modelDownload=(()=>{try{return performance.getEntriesByType('resource').filter((x:any)=>x.name.includes('/models/')).map((x:any)=>({name:x.name,duration:x.duration,transferSize:x.transferSize,encodedBodySize:x.encodedBodySize}));}catch{return [];}})();
  report.note='模型下载由 prepare-model 在浏览器运行前完成；真实网络下载用日志记录，不与浏览器加载混合比较。WebGPU 失败的轮次保留为 WASM 降级样本，不计为 GPU 成绩。该小型人工相关性集仅用于可复现工程验收，不能代表通用中文检索质量。';
  return report;
}
const mean=(xs:number[])=>xs.reduce((a,b)=>a+b,0)/xs.length;
