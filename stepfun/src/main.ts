import './style.css';
import {Engine,api,saveFile} from './engine.ts';
import {chunks,hash,type Chunk,type Precision,type Backend} from './shared.ts';
import {benchmark} from './benchmark.ts';
const app=document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML=`
<header><a class="brand" href="/"><span class="brand-mark">屿</span>知屿 <small>EDGE KNOWLEDGE</small></a><span class="private">◉ 浏览器本地推理</span></header>
<main><section class="intro"><div><p class="eyebrow">SEEKDB × ON-DEVICE AI</p><h1>让知识，<br>在你的设备上相遇。</h1><p class="lede">导入文档，用自然语言找回答案。模型在浏览器运行，<br>由 seekdb 完成向量与全文混合检索。</p></div><div class="intro-note"><span>01 / LOCAL FIRST</span><p>文档与查询向量<br>均在你的浏览器生成</p><div class="orbit">知<span>↗</span></div></div></section>
<section class="statusbar"><span><i id="db-dot"></i><b id="db-status">连接 seekdb…</b></span><span id="backend">模型尚未加载</span><span id="counter">0 个文本片段</span></section>
<div class="workspace"><aside class="panel settings"><h2><span>01</span> 知识库与模型</h2><label>当前知识库<input id="kb" value="personal" maxlength="26" pattern="[a-z][a-z0-9_]*" aria-label="知识库名称"></label><p class="hint">小写字母、数字或下划线。输入新名称即可切换。</p><label>模型精度<select id="precision"><option value="q8">INT8 · 推荐</option><option value="fp32">FP32 · 测试基线</option></select></label><label>推理后端<select id="device"><option value="auto">自动 · 优先 WebGPU</option><option value="wasm">WASM · 验证降级</option><option value="webgpu">请求 WebGPU · 支持失败降级</option></select></label><button class="button primary" id="load">加载本地模型</button><p class="hint" id="model-note">BGE small 中文模型 / 512 维<br>首次运行前请按 README 下载模型文件。</p><div class="divider"></div><h2><span>02</span> 导入文本</h2><label class="dropzone">＋ 选择 TXT / MD 文件<input id="files" type="file" accept=".txt,.md,text/plain,text/markdown" multiple></label><label>或直接粘贴<textarea id="text" rows="5" placeholder="把需要记住的内容放在这里…"></textarea></label><div class="button-row"><button class="button primary" id="import">导入知识库</button><button class="button ghost" id="sample">示例语料</button></div><progress id="progress" value="0" max="1"></progress><p class="hint" id="progress-note">等待导入。向量与原文将写入本地 seekdb。</p></aside>
<section class="panel search-panel"><div class="section-top"><h2><span>03</span> 找到你需要的知识</h2><select id="search-mode" aria-label="检索模式"><option value="hybrid">混合检索 · RRF</option><option value="vector">仅向量 · 对照</option></select></div><form id="search-form"><div class="query-box"><input id="query" placeholder="例如：浏览器不支持 WebGPU 怎么办？" autocomplete="off" aria-label="查询内容"><button class="button primary" id="search">检索 ↗</button></div></form><div class="suggestions"><button data-query="浏览器不支持 WebGPU 怎么办？">浏览器推理</button><button data-query="怎样让新导入文档能立即被找到？">索引刷新</button><button data-query="量化如何影响模型大小和检索质量？">模型量化</button></div><p class="hint" id="latency">每条结果均可展开原文与来源。</p><div id="results" aria-live="polite"><div class="empty"><div>⌕</div><h3>你的下一次发现，从这里开始</h3><p>加载模型、导入文档，再输入一个问题。</p></div></div></section></div>
<section class="panel experiment"><div><p class="eyebrow">REPRODUCIBLE BY DESIGN</p><h2>把性能提升，交给实测。</h2><p class="hint">同一语料与查询集，比较 FP32 / INT8、批大小与后端。每种配置重复三次，保留失败与降级记录。<br>测试会载入两个精度的模型，可能需要数分钟。</p></div><div class="button-row"><button class="button ghost" id="run-benchmark">运行完整评测</button><button class="button ghost" id="export" disabled>导出测试报告</button><button class="button ghost" id="log-export">导出操作记录</button></div><p id="eval-status" class="hint">尚未运行。报告将生成 Recall@5、nDCG@5 和分阶段耗时。</p><pre id="eval-preview" aria-label="评测结果预览"></pre></section>
<div id="message" role="status" aria-live="polite"></div></main><footer>知屿 · 浏览器内向量化 · seekdb 数据库内混合检索 <span>STEPFUN / 2026</span></footer>`;
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
let engine=new Engine(),loaded=false,busy=false,databaseReady=false,report:any;
const logs:any[]=[];
const val=(id:string)=>$(id) as HTMLInputElement;
const progress=(done:number,total:number,note:string)=>{($('progress') as HTMLProgressElement).max=total||1;($('progress') as HTMLProgressElement).value=done;$('progress-note').textContent=note;};
function message(s:string,error=false){$('message').textContent=s;$('message').className=error?'error':'success';}
function modelProgress(p:any){
  if(p.stage==='embedding')progress(p.done,p.total,`本地向量化 ${p.done} / ${p.total}`);
  else if(p.message)$('model-note').textContent=p.message;
  else if(p.status==='progress')$('model-note').textContent=`读取模型 ${p.file} · ${Math.round(p.progress||0)}%`;
}
async function state(){
  if(!databaseReady||!loaded)return;
  const s=await api('status',{kb:val('kb').value,profile:engine.profile});
  $('counter').textContent=`${s.count} 个文本片段`;
}
function controls(disabled:boolean){
  for(const id of ['load','import','sample','search','files','kb','precision','device','run-benchmark']) ($<HTMLButtonElement>(id)).disabled=disabled;
}
async function task(work:()=>Promise<void>){if(busy)return;busy=true;controls(true);try{await work();}catch(e){message(String(e),true);}finally{busy=false;controls(false);}}
function requireReady(){if(!databaseReady)throw Error('seekdb 尚未就绪，请查看数据库状态');if(!loaded)throw Error('请先加载本地模型');}
$('load').onclick=()=>task(async()=>{
  loaded=false;
  const info=await engine.init(val('precision').value as Precision,val('device').value as Backend,modelProgress);
  loaded=true;$('backend').textContent=`${info.backend.toUpperCase()} · ${info.precision.toUpperCase()}`;
  $('model-note').textContent=`${info.manifest.id}\n版本 ${info.manifest.revision.slice(0,12)} · 加载含预热 ${info.loadMs.toFixed(0)} ms\n${info.fallback||'后端已就绪'}`;
  logs.push({type:'model-load',at:new Date().toISOString(),...info});await state();message('模型已准备好，可以导入文档。');
});
for(const id of ['precision','device'])$(id).onchange=()=>{loaded=false;$('backend').textContent='配置已变更，请重新加载模型';$('results').replaceChildren();};
$('kb').onchange=()=>task(async()=>{$('results').replaceChildren();await state();message(`已切换到知识库 ${val('kb').value}`);});
async function importDocs(sources:{id?:string;name:string;text:string}[]){
  requireReady();const kb=val('kb').value,profile=engine.profile;
  const docs:Chunk[]=[];
  for(const source of sources){
    const sourceId=source.id || await hash(source.name+'\n'+source.text);
    const parts=chunks(source.text);
    for(let i=0;i<parts.length;i++)docs.push({id:await hash(sourceId+'|'+i+'|'+parts[i]),sourceId,source:source.name,text:parts[i],chunk:i});
  }
  if(!docs.length)throw Error('没有可导入的文本');
  // Each bounded request completes one incremental group and refreshes once after its writes.
  const t=performance.now();let embeddingMs=0,writeMs=0,refreshMs=0;
  for(let start=0;start<docs.length;start+=256){
    const batch=docs.slice(start,start+256);
    const emb=await engine.embed(batch.map(d=>d.text),false,8,p=>{if(p.stage==='embedding')progress(start+p.done,docs.length,`本地向量化 ${start+p.done}/${docs.length}`);});
    embeddingMs+=emb.embeddingMs;
    progress(start+batch.length,docs.length,'写入 seekdb 并刷新索引…');
    const r=await api('import',{kb,profile,docs:batch.map((d,i)=>({...d,vector:emb.vectors[i]}))});writeMs+=r.writeMs;refreshMs+=r.refreshMs;
  }
  await state();const entry={type:'import',at:new Date().toISOString(),kb,profile,chunks:docs.length,embeddingMs,writeMs,refreshMs,totalMs:performance.now()-t};logs.push(entry);
  progress(docs.length,docs.length,`完成 ${docs.length} 片段 · 向量化 ${embeddingMs.toFixed(0)} ms · 写入 ${writeMs.toFixed(0)} ms · 刷新 ${refreshMs.toFixed(0)} ms`);message('导入完成，索引已刷新。相同内容重复导入会更新原记录。');
}
$('import').onclick=()=>task(async()=>{
  const sources:{name:string;text:string}[]=[];
  for(const file of Array.from(($('files') as HTMLInputElement).files||[])){
    if(file.size>5*1024*1024)throw Error('单个文件最大 5 MB');sources.push({name:file.name,text:await file.text()});
  }
  if(val('text').value.trim())sources.push({name:'粘贴文本',text:val('text').value});
  await importDocs(sources);
});
$('sample').onclick=()=>task(async()=>{
  const data=await api('eval-data');await importDocs(data.documents.map((d:any)=>({id:d.id,name:d.source,text:d.text})));
});
$('search-form').onsubmit=e=>{e.preventDefault();void task(async()=>{
  requireReady();const text=val('query').value.trim();if(!text)throw Error('请输入查询内容');
  const kb=val('kb').value,profile=engine.profile,t=performance.now();
  const emb=await engine.embed([text],true,1);
  const r=await api('search',{kb,profile,text,vector:emb.vectors[0],k:10,mode:val('search-mode').value});
  $('results').replaceChildren();
  r.hits.forEach((hit:any,i:number)=>{
    const article=document.createElement('article');article.className='hit';
    const top=document.createElement('div');top.className='hit-top';
    const rank=document.createElement('span');rank.className='rank';rank.textContent=String(i+1).padStart(2,'0');
    const source=document.createElement('strong');source.textContent=String(hit.metadata.source||hit.metadata.sourceId||hit.id);
    const label=document.createElement('small');label.textContent=`片段 ${Number(hit.metadata.chunk||0)+1}`;
    top.append(rank,source,label);
    const text=document.createElement('p');text.textContent=hit.text.length>180?hit.text.slice(0,180)+'…':hit.text;
    const detail=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
    summary.textContent='查看完整原文与来源';pre.textContent=hit.text+'\n\n'+JSON.stringify(hit.metadata,null,2);detail.append(summary,pre);article.append(top,text,detail);$('results').append(article);
  });
  if(!r.hits.length)$('results').textContent='当前知识库没有匹配结果，请先导入文档。';
  await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
  const totalMs=performance.now()-t;
  $('latency').textContent=`找到 ${r.hits.length} 条结果 · 查询向量化 ${emb.embeddingMs.toFixed(0)} ms · seekdb ${r.seekdbMs.toFixed(0)} ms · 到结果展示 ${totalMs.toFixed(0)} ms`;
  logs.push({type:'query',at:new Date().toISOString(),kb,profile,mode:val('search-mode').value,embeddingMs:emb.embeddingMs,seekdbMs:r.seekdbMs,totalMs});
});};
for(const button of document.querySelectorAll<HTMLButtonElement>('[data-query]'))button.onclick=()=>{val('query').value=button.dataset.query!;};
$('run-benchmark').onclick=()=>task(async()=>{
  if(!databaseReady)throw Error('seekdb 尚未就绪');
  // Release the interactive model to avoid holding two model instances in GPU memory.
  engine.dispose();loaded=false;engine=new Engine();$('backend').textContent='评测进行中';
  report=await benchmark(s=>$('eval-status').textContent=s);
  const saved=await api('report',report);$('export').removeAttribute('disabled');
  const completed=report.runs.filter((x:any)=>x.completed).length;
  $('eval-status').textContent=`评测结束：${completed}/${report.runs.length} 轮完成，报告已保存至 ${saved.file}。WebGPU 降级信息保留在报告中。请导出并检查结果。`;
  $('backend').textContent='评测结束，请重新加载交互模型';
  message(completed===report.runs.length?'全部测试轮次已完成。':'部分轮次失败，详情见测试报告。',completed!==report.runs.length);
});
$('export').onclick=()=>saveFile('stepfun-benchmark.json',JSON.stringify(report,null,2));
$('log-export').onclick=()=>saveFile('stepfun-operations.json',JSON.stringify({at:new Date().toISOString(),browser:navigator.userAgent,logs},null,2));
async function health(){
  try{const h=await api('health');databaseReady=h.ready;$('db-status').textContent=h.ready?`seekdb 已连接 · ${h.mode}`:(h.error||'seekdb 正在启动…');$('db-dot').className=h.ready?'ready':'pending';}
  catch{$('db-status').textContent='seekdb 暂未连接';}
  if(!databaseReady)setTimeout(()=>void health(),3000);
}
void health();
