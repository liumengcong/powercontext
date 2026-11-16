// Infrastructure smoke tests use deterministic synthetic vectors solely to verify DB plumbing.
// They are explicitly not browser embedding performance or semantic-quality measurements.
import 'dotenv/config';
import assert from 'node:assert/strict';
import {resolve} from 'node:path';
import {mkdir,writeFile} from 'node:fs/promises';
import {KnowledgeStore} from '../server/store.ts';
import {hash,DIMENSION} from '../src/shared.ts';
process.env.SEEKDB_BINDINGS_CACHE_DIR ||= resolve('./runtime/bindings');
const store=new KnowledgeStore();
const report:any={createdAt:new Date().toISOString(),kind:'real-seekdb-infrastructure-synthetic-vectors',cases:[]};
const profile=await hash('integration-infrastructure-v1');
const vector=Array.from({length:DIMENSION},(_,i)=>i===0?1:0);
const kb='smoke_a', kb2='smoke_b';
const sourceId='infra-'+Date.now();
const doc={id:sourceId,sourceId,source:'数据库基础设施测试',chunk:0,text:'星辰测试词 索引刷新 混合检索',vector};
async function check(name:string,fn:()=>Promise<void>){const t=performance.now();await fn();report.cases.push({name,passed:true,ms:performance.now()-t});console.log('PASS',name);}
try {
  await store.init();report.version=store.version;
  const before=(await store.status(kb,profile)).count;
  await check('写入并刷新后数据库混合检索包含新增文档',async()=>{
    await store.write(kb,profile,[doc]);
    const r=await store.search(kb,profile,'星辰测试词',vector,10);
    assert.ok(r.hits.some(x=>x.id===sourceId));
    const result=await store.queue.run(async()=>{
      const c=await (store as any).client.getCollection({name:`edge_${kb}_${profile.slice(0,32)}`,embeddingFunction:null});
      return c.hybridSearch({query:{whereDocument:{$contains:'星辰测试词'}},nResults:10,include:['documents','metadatas']});
    });
    assert.ok(result.ids[0].includes(sourceId),'全文通路索引未刷新或不支持');
  });
  await check('重复导入采用 upsert 不增加记录数',async()=>{
    await store.write(kb,profile,[doc]);assert.equal((await store.status(kb,profile)).count,before+1);
  });
  await check('切换知识库保持结果隔离',async()=>{
    const r=await store.search(kb2,profile,'星辰测试词',vector,10);assert.ok(r.hits.every(x=>x.id!==sourceId));
  });
  await check('配置指纹隔离',async()=>{
    const r=await store.search(kb,await hash('other-model-config'),'星辰测试词',vector,10);assert.ok(r.hits.every(x=>x.id!==sourceId));
  });
  await check('并发混合查询不串会话',async()=>{
    const rs=await Promise.all(Array.from({length:5},()=>store.search(kb,profile,'星辰测试词',vector,10)));
    assert.ok(rs.every(r=>r.hits.some(x=>x.id===sourceId)));
  });
  await store.close();
  await check('关闭后重开可检索已有内容',async()=>{
    const reopened=new KnowledgeStore();
    try{await reopened.init();const r=await reopened.search(kb,profile,'星辰测试词',vector,10);assert.ok(r.hits.some(x=>x.id===sourceId));}
    finally{await reopened.close();}
  });
  report.completed=true;
} catch(e){report.completed=false;report.error=String(e);process.exitCode=1;console.error(e);}
finally{await store.close().catch(()=>{});await mkdir('reports',{recursive:true});await writeFile('reports/integration.json',JSON.stringify(report,null,2));}
