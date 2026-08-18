import { json, readBody, loadUsers, saveUsers, hashPassword, isAdminOf, now } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  const users = await loadUsers(env);
  if (!isAdminOf(users, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }
  const name = String(body.username || '').trim();
  const pwd = String(body.password || '');
  if (name.length < 2 || pwd.length < 6) {
    return json(400, { ok: false, error: '用户名至少2位、密码至少6位' });
  }
  if (users[name]) return json(409, { ok: false, error: '账户已存在' });
  const { salt, hash } = await hashPassword(pwd);
  users[name] = {
    salt, hash, banned: false, is_admin: !!body.is_admin, created: now(),
  };
  await saveUsers(env, users);
  return json(200, { ok: true });
}
