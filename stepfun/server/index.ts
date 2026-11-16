import 'dotenv/config';
import express from 'express';
import { z } from 'zod';
import { resolve, dirname } from 'node:path';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { KnowledgeStore } from './store.ts';
import { DIMENSION } from '../src/shared.ts';

process.env.SEEKDB_BINDINGS_CACHE_DIR ||= resolve('./runtime/bindings');
const app = express(), store = new KnowledgeStore();
let ready = false, startupError = '';
const port = Number(process.env.PORT || 3000), host = process.env.HOST || '127.0.0.1';
app.use((req, res, next) => {
  const origin = req.headers.origin;
  if (origin && origin !== `http://${req.headers.host}` && origin !== `https://${req.headers.host}`) {
    res.status(403).json({error:'只接受同源请求'}); return;
  }
  res.setHeader('X-Content-Type-Options','nosniff'); next();
});
app.use(express.json({limit:'24mb'}));
const base = z.object({kb:z.string().regex(/^[a-z][a-z0-9_]{0,25}$/),profile:z.string().regex(/^[a-f0-9]{64}$/)});
const vector = z.array(z.number().finite()).length(DIMENSION).refine(v=>v.some(x=>x!==0),'不接受全零向量');
app.get('/api/health', (_req,res) => res.json({ready,error:startupError,version:store.version,mode:process.env.SEEKDB_MODE || 'embedded'}));
app.use('/api', (req,res,next) => {
  if (!ready && req.path !== '/health') {res.status(503).json({error:startupError || 'seekdb 正在启动，首次使用会下载原生组件，请稍候'});return;}
  next();
});
app.post('/api/status', async(req,res)=>res.json(await store.status(...params(base.parse(req.body)))));
app.post('/api/import', async(req,res)=>{
  const b=base.extend({docs:z.array(z.object({id:z.string().max(128),sourceId:z.string().max(200),source:z.string().max(500),text:z.string().min(1).max(4000),chunk:z.number().int().min(0),vector})).min(1).max(512),refreshEachBatch:z.boolean().default(false)}).parse(req.body);
  res.json(await store.write(b.kb,b.profile,b.docs,b.refreshEachBatch));
});
app.post('/api/search', async(req,res)=>{
  const b=base.extend({text:z.string().min(1).max(1000),vector,k:z.number().int().min(1).max(100).default(10),mode:z.enum(['hybrid','vector']).default('hybrid')}).parse(req.body);
  res.json(await store.search(b.kb,b.profile,b.text,b.vector,b.k,b.mode));
});
app.post('/api/refresh',async(req,res)=>res.json(await store.refresh(...params(base.parse(req.body)))));
app.get('/api/eval-data',async(_req,res)=>res.json(JSON.parse(await readFile(resolve('data/eval.json'),'utf8'))));
app.post('/api/report',async(req,res)=>{
  // Only diagnostics explicitly exported by the local application. No credentials are collected.
  if (!req.body?.runs || !Array.isArray(req.body.runs)) {res.status(400).json({error:'无效报告'});return;}
  await mkdir('reports',{recursive:true});
  const name=`benchmark-${new Date().toISOString().replace(/[:.]/g,'-')}.json`;
  await writeFile(resolve('reports',name),JSON.stringify(req.body,null,2)); res.json({file:`reports/${name}`});
});
function params(x:{kb:string;profile:string}):[string,string]{return [x.kb,x.profile];}
const require=createRequire(import.meta.url);
const ortDir=dirname(dirname(require.resolve('onnxruntime-web')));
app.use('/ort',express.static(resolve(ortDir,'dist')));
app.use('/models',express.static(resolve('public/models')));
if (process.argv.includes('--production')) app.use(express.static(resolve('dist')));
else {
  const {createServer}=await import('vite');
  const vite=await createServer({server:{middlewareMode:true},appType:'spa'});
  app.use(vite.middlewares);
}
app.use((err:unknown,_req:express.Request,res:express.Response,_next:express.NextFunction)=>{
  console.error(err);
  res.status(err instanceof z.ZodError?400:500).json({error:err instanceof Error?err.message:'请求失败'});
});
const server=app.listen(port,host,()=>console.log(`知屿：http://${host}:${port}`));
void store.init().then(()=>{ready=true;console.log(`seekdb ready: ${store.version}`);}).catch(err=>{startupError=String(err);console.error(startupError);});
for(const sig of ['SIGINT','SIGTERM'] as const) process.once(sig,()=>{server.close();void store.close().finally(()=>process.exit(0));});
