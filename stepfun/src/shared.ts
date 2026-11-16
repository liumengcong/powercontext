export const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
export const DIMENSION = 512;
export const QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：';
export type Precision = 'fp32' | 'q8';
export type Backend = 'auto' | 'webgpu' | 'wasm';
export interface ModelManifest {
  id: string; revision: string; dimension: number;
  files: { path: string; bytes: number; sha256: string }[];
  modelBytes: Record<Precision, number>;
}
export interface Chunk { id: string; sourceId: string; source: string; text: string; chunk: number; }
export interface SearchHit { id: string; text: string; metadata: Record<string, unknown>; score: number | null; }
export function chunks(text: string, size = 240, overlap = 40): string[] {
  if (size <= overlap || overlap < 0) throw new Error('分块长度必须大于重叠长度');
  const chars = Array.from(text.replace(/\r\n?/g, '\n').trim());
  const result: string[] = [];
  for (let start = 0; start < chars.length; start += size - overlap) {
    const part = chars.slice(start, start + size).join('').trim();
    if (part) result.push(part);
    if (start + size >= chars.length) break;
  }
  return result;
}
export async function hash(value: string) {
  const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return [...new Uint8Array(buffer)].map(x => x.toString(16).padStart(2, '0')).join('');
}
export function quality(ranked: string[], relevant: Record<string, number>, k = 5) {
  // Multiple chunks of the same source count as one source, preserving DB order.
  const unique = [...new Set(ranked)].slice(0, k);
  const positives = Object.keys(relevant).filter(x => relevant[x] > 0);
  const dcg = unique.reduce((s, id, i) => s + (2 ** (relevant[id] || 0) - 1) / Math.log2(i + 2), 0);
  const ideal = Object.values(relevant).filter(x => x > 0).sort((a, b) => b - a).slice(0, k)
    .reduce((s, grade, i) => s + (2 ** grade - 1) / Math.log2(i + 2), 0);
  return { recall: positives.length ? unique.filter(id => relevant[id] > 0).length / positives.length : 0,
    ndcg: ideal ? dcg / ideal : 0 };
}
export const median = (items: number[]) => {
  if (!items.length) return null;
  const a = [...items].sort((a,b) => a-b), m = Math.floor(a.length/2);
  return a.length % 2 ? a[m] : (a[m-1] + a[m]) / 2;
};
