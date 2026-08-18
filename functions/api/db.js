// GET /api/db —— 返回完整 OPLUS 数据库（JSON）
// 数据源：KV（xsroot_db_v1，管理员上传 / 每日工作流自动更新）；
//         未上传时回退 GitHub 仓库中的数据库文件。
import { json } from './_lib.js';

const KV_DB_KEY = 'xsroot_db_v1';
const DB_NAME = 'OPLUS全部版本信息_CN_标准格式.json';

function respond(raw) {
  return new Response(raw, {
    status: 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'no-store',
    },
  });
}

export async function onRequestGet({ env }) {
  try {
    const raw = await env.USERS_KV.get(KV_DB_KEY);
    if (raw) return respond(raw);
  } catch (e) { /* 忽略 KV 错误，走回退 */ }

  // 回退：GitHub 仓库中的数据库（每日工作流提交更新）
  try {
    const url = 'https://raw.githubusercontent.com/xs807/oplus-rom-site/main/database/' +
      encodeURIComponent(DB_NAME);
    const r = await fetch(url, { headers: { 'User-Agent': 'xsroot-site' } });
    if (r.ok) {
      const t = await r.text();
      try { await env.USERS_KV.put(KV_DB_KEY, t); } catch (e) { /* 缓存失败忽略 */ }
      return respond(t);
    }
  } catch (e) { /* 忽略 */ }

  return json(404, { ok: false, error: '数据库尚未上传' });
}
