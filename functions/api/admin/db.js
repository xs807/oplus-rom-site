// POST /api/admin/db —— 管理员上传/更新数据库到 KV
// body: {"admin_user":"...", "admin_token":"...", "database":"<完整JSON文本>"} 或 {"base64":"..."}
import { json, readBody, loadUsers, isAdminOf } from '../_lib.js';

const KV_DB_KEY = 'xsroot_db_v1';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  const users = await loadUsers(env);
  if (!isAdminOf(users, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }

  let text = '';
  if (body.database) {
    text = String(body.database);
  } else if (body.base64) {
    try {
      text = new TextDecoder().decode(
        Uint8Array.from(atob(String(body.base64)), c => c.charCodeAt(0)),
      );
    } catch {
      return json(400, { ok: false, error: 'base64 解析失败' });
    }
  } else {
    return json(400, { ok: false, error: '缺少 database 或 base64 字段' });
  }

  let parsed = null;
  try {
    parsed = JSON.parse(text);
  } catch {
    return json(400, { ok: false, error: '数据库 JSON 解析失败' });
  }
  const rows = Array.isArray(parsed) ? parsed : (parsed.结果 || []);
  if (!Array.isArray(rows) || rows.length === 0) {
    return json(400, { ok: false, error: '数据库内容为空或格式错误' });
  }

  try {
    await env.USERS_KV.put(KV_DB_KEY, text);
  } catch (e) {
    return json(500, { ok: false, error: 'KV 写入失败：' + e.message });
  }
  return json(200, { ok: true, entries: rows.length });
}
