import { json, readBody, loadUsers, saveUsers, isAdminOf, now } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  const users = await loadUsers(env);
  if (!isAdminOf(users, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }
  const name = String(body.username || '').trim();
  if (!users[name]) return json(404, { ok: false, error: '账户不存在' });
  users[name].banned = true;
  users[name].ban_reason = String(body.reason || '管理员封禁');
  users[name].banned_at = now();
  await saveUsers(env, users);
  return json(200, { ok: true });
}
