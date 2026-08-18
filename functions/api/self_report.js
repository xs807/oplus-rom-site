import { json, readBody, loadUsers, saveUsers, nameOfToken, now } from './_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  const users = await loadUsers(env);
  const name = nameOfToken(users, body.token || '');
  if (!name) return json(401, { ok: false, error: '令牌无效' });
  users[name].banned = true;
  users[name].ban_reason = '检测到逆向工具（客户端自报）';
  users[name].banned_at = now();
  await saveUsers(env, users);
  return json(200, { ok: true });
}
