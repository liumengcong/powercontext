import { SeekdbClient, SeekdbAdminClient, Schema, FulltextIndexConfig, VectorIndexConfig, type Collection } from 'seekdb';
import { DIMENSION, type Chunk, type SearchHit } from '../src/shared.ts';
import { resolve } from 'node:path';
import { mkdir } from 'node:fs/promises';

// seekdb-js hybridSearch uses session variables. Serialize each full operation,
// including all SDK SQL calls, so simultaneous HTTP requests cannot interleave them.
export class SerialQueue {
  private tail: Promise<unknown> = Promise.resolve();
  run<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(fn);
    this.tail = result.catch(() => {});
    return result;
  }
}
export class KnowledgeStore {
  readonly queue = new SerialQueue();
  private client!: SeekdbClient;
  private collections = new Map<string, Collection>();
  version = '';
  private profileKey(profile: string) {
    // Fingerprint includes revision, precision, pooling, query instruction and chunking.
    if (!/^[a-f0-9]{64}$/.test(profile)) throw new Error('无效模型配置');
    return profile;
  }
  private key(kb: string, profile: string) {
    if (!/^[a-z][a-z0-9_]{0,25}$/.test(kb)) throw new Error('知识库名只允许小写字母、数字和下划线');
    return `edge_${kb}_${this.profileKey(profile).slice(0,32)}`;
  }
  async init() {
    const embedded = process.env.SEEKDB_MODE !== 'server';
    const database = process.env.SEEKDB_DATABASE || 'stepfun';
    const path = resolve(process.env.SEEKDB_PATH || './runtime/seekdb');
    if (embedded) await mkdir(path, { recursive: true });
    const connection = embedded ? { path } : {
      host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881),
      user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '',
    };
    const admin = new SeekdbAdminClient(connection);
    try {
      if (!(await admin.listDatabases()).some(x => x.name === database)) await admin.createDatabase(database);
    } finally { await admin.close(); }
    this.client = new SeekdbClient({ ...connection, database });
    const rows = await this.client.execute('SELECT VERSION() AS version');
    this.version = String(rows?.[0]?.version || 'unknown');
  }
  private async collection(kb: string, profile: string) {
    const name = this.key(kb, profile);
    let c = this.collections.get(name);
    if (c) return c;
    if (await this.client.hasCollection(name)) c = await this.client.getCollection({ name, embeddingFunction: null });
    else c = await this.client.createCollection({ name, schema: new Schema({
      fulltextIndex: new FulltextIndexConfig('ngram', { ngram_token_size: 2 }),
      vectorIndex: new VectorIndexConfig({ hnsw: { dimension: DIMENSION, distance: 'cosine' }, embeddingFunction: null }),
    }), embeddingFunction: null });
    this.collections.set(name, c);
    return c;
  }
  async status(kb: string, profile: string) {
    return this.queue.run(async () => {
      const c = await this.collection(kb, profile);
      return { count: Number(await c.count()), collection: c.name, version: this.version };
    });
  }
  async write(kb: string, profile: string, docs: (Chunk & { vector: number[] })[], refreshEachBatch = false) {
    return this.queue.run(async () => {
      const c = await this.collection(kb, profile);
      const start = performance.now(); let refreshMs = 0;
      for (let i = 0; i < docs.length; i += 64) {
        const batch = docs.slice(i, i + 64);
        await c.upsert({ ids: batch.map(d => d.id), documents: batch.map(d => d.text),
          embeddings: batch.map(d => d.vector), metadatas: batch.map(({ vector, text, ...m }) => ({...m, profile})) });
        if (refreshEachBatch) { const t = performance.now(); await c.refresh_index(); refreshMs += performance.now()-t; }
      }
      const writeMs = performance.now()-start-refreshMs;
      if (!refreshEachBatch) { const t = performance.now(); await c.refresh_index(); refreshMs += performance.now()-t; }
      // Verify immediately visible vector results. The full-text branch is verified by integration checks.
      if (docs.length) {
        const probe = docs[docs.length-1];
        const visible = await c.query({ queryEmbeddings: [probe.vector], nResults: 1, where: { sourceId: { $eq: probe.sourceId } } });
        if (!visible.ids[0]?.length) throw new Error('写入完成，但索引尚不可见，请重试刷新');
      }
      return { writeMs, refreshMs, count: Number(await c.count()) };
    });
  }
  async search(kb: string, profile: string, text: string, vector: number[], k = 10, mode: 'hybrid' | 'vector' = 'hybrid') {
    return this.queue.run(async () => {
      const c = await this.collection(kb, profile), t = performance.now();
      // Both retrieval branches and RRF ranking execute in seekdb via DBMS_HYBRID_SEARCH.GET_SQL.
      const result = mode === 'vector'
        ? await c.query({ queryEmbeddings: [vector], nResults: k, include: ['documents','metadatas','distances'] })
        : await c.hybridSearch({
          query: { whereDocument: { $contains: text }, nResults: 50 },
          knn: { queryEmbeddings: [vector], nResults: 50 },
          rank: { rrf: { rankConstant: 60, rankWindowSize: 50 } }, nResults: k,
          include: ['documents','metadatas','distances'],
        });
      const seekdbMs = performance.now() - t;
      const hits: SearchHit[] = (result.ids[0] || []).map((id, i) => ({ id,
        text: result.documents?.[0]?.[i] || '', metadata: result.metadatas?.[0]?.[i] || {},
        score: result.distances?.[0]?.[i] ?? null }));
      return { hits, seekdbMs };
    });
  }
  async refresh(kb: string, profile: string) {
    return this.queue.run(async () => { const c = await this.collection(kb, profile); await c.refresh_index(); return {count: Number(await c.count())}; });
  }
  async close() { if (this.client) await this.client.close(); }
}
