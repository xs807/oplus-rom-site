import { json, readBody, loadUsers, isAdminOf } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  const users = await loadUsers(env);
  if (!isAdminOf(users, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }
  const out = Object.entries(users).map(([name, u]) => ({
    username: name,
    banned: !!u.banned,
    is_admin: !!u.is_admin,
    created: u.created,
    last_login: u.last_login,
    ban_reason: u.ban_reason || '',
  }));
  return json(200, { ok: true, users: out });
}
