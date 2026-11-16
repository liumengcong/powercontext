import {readFile,writeFile} from 'node:fs/promises';
const file=process.argv[2];if(!file)throw Error('用法：npm run report -- reports/benchmark-xxx.json');
const report=JSON.parse(await readFile(file,'utf8'));
const median=xs=>{const a=xs.sort((a,b)=>a-b),m=Math.floor(a.length/2);return a.length?a.length%2?a[m]:(a[m-1]+a[m])/2:null;};
const keys=[...new Set(report.runs.map(x=>x.name))];
const rows=keys.map(name=>{
  const all=report.runs.filter(x=>x.name===name),valid=all.filter(x=>x.completed),matched=valid.filter(x=>x.actualBackend===x.backend);
  const queries=matched.flatMap(x=>x.queries),avg=key=>queries.length?queries.reduce((s,q)=>s+q.metrics[key],0)/queries.length:null;
  return {name,complete:valid.length,total:all.length,requestedBackend:all[0].backend,actualBackends:[...new Set(valid.map(x=>x.actualBackend))].join('+'),matchedRuns:matched.length,
    modelBytes:matched[0]?.modelBytes??null,loadMedianMs:median(matched.map(x=>x.loadMs)),documentsPerSecond:median(matched.map(x=>x.documentsPerSecond)),writeMedianMs:median(matched.map(x=>x.writeMs)),refreshMedianMs:median(matched.map(x=>x.refreshMs)),seekdbMedianMs:median(queries.map(x=>x.seekdbMs)),e2eMedianMs:median(queries.map(x=>x.e2eMs)),recall5:avg('recall'),ndcg5:avg('ndcg')};
});
const fp=rows.find(x=>x.name==='fp32_wasm_b1'),q8=rows.find(x=>x.name==='q8_wasm_b1');
const summary={device:report.device,dataset:report.dataset,seekdb:report.seekdb,rows,
 comparison:fp&&q8&&fp.modelBytes&&q8.modelBytes?{compression:fp.modelBytes/q8.modelBytes,recallDelta:q8.recall5-fp.recall5,ndcgDelta:q8.ndcg5-fp.ndcg5}:null,
 warning:'只将实际后端与请求后端相符且完成的轮次计入该配置；WebGPU 降级样本在原始报告中保留。'};
await writeFile(file.replace(/\.json$/,'.summary.json'),JSON.stringify(summary,null,2));
const columns=Object.keys(rows[0]);
await writeFile(file.replace(/\.json$/,'.csv'),columns.join(',')+'\n'+rows.map(x=>columns.map(k=>JSON.stringify(x[k]??'')).join(',')).join('\n')+'\n');
const fmt=x=>x===null||x===undefined?'未测得':typeof x==='number'?x.toFixed(3):String(x);
const md=['|配置|实际后端|有效轮次|ONNX字节|加载ms|文档/秒|seekdb ms|端到端ms|Recall@5|nDCG@5|','|---|---|---|---|---|---|---|---|---|---|',...rows.map(x=>`|${x.name}|${x.actualBackends}|${x.matchedRuns}/${x.total}|${x.modelBytes??'未测得'}|${fmt(x.loadMedianMs)}|${fmt(x.documentsPerSecond)}|${fmt(x.seekdbMedianMs)}|${fmt(x.e2eMedianMs)}|${fmt(x.recall5)}|${fmt(x.ndcg5)}|`)];
if(summary.comparison)md.push(`\n压缩比 ${fmt(summary.comparison.compression)}；Recall@5 变化 ${fmt(summary.comparison.recallDelta)}；nDCG@5 变化 ${fmt(summary.comparison.ndcgDelta)}。`);
md.push('\n'+summary.warning);
await writeFile(file.replace(/\.json$/,'.md'),md.join('\n')+'\n');console.log(md.join('\n'));
