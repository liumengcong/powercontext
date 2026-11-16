import test from 'node:test';
import assert from 'node:assert/strict';
import {chunks,quality,median,hash} from '../src/shared.ts';
import {SerialQueue} from '../server/store.ts';
test('分块覆盖原文且不拆开 Unicode 代理对',()=>{
  const text='中文😀ABC'.repeat(100);const parts=chunks(text,40,8);
  assert.equal(parts[0],Array.from(text).slice(0,40).join(''));
  assert.equal(parts.slice(1).reduce((s,p)=>s+Array.from(p).slice(8).join(''),parts[0]),text);
  assert.ok(parts.every(x=>Array.from(x).length<=40));
  assert.deepEqual(chunks('   '),[]);
  assert.throws(()=>chunks('x',10,10));
});
test('重复片段不能使 Recall 和 nDCG 虚高',()=>{
  const q=quality(['a','a','b','x'],{a:3,b:1},2);
  assert.equal(q.recall,1);assert.equal(q.ndcg,1);
  assert.deepEqual(quality(['irrelevant'],{a:1},5),{recall:0,ndcg:0});
});
test('原始样本中位数保留偶数样本规则',()=>{
  assert.equal(median([100,1,2,3]),2.5);assert.equal(median([3,1,2]),2);assert.equal(median([]),null);
});
test('ID 哈希稳定、内容变更不会覆盖旧文档版本',async()=>{
  assert.equal(await hash('a'),await hash('a'));assert.notEqual(await hash('a'),await hash('b'));
});
test('队列将完整会话操作串行化，失败后仍执行后续任务',async()=>{
  const q=new SerialQueue(),events:string[]=[];
  const a=q.run(async()=>{events.push('a-start');await new Promise(r=>setTimeout(r,15));events.push('a-end');});
  const b=q.run(async()=>{events.push('b');throw Error('expected');});
  const c=q.run(async()=>{events.push('c');return 3;});
  const result=await Promise.allSettled([a,b,c]);
  assert.deepEqual(events,['a-start','a-end','b','c']);assert.equal(result[1].status,'rejected');assert.equal(result[2].status,'fulfilled');
});
