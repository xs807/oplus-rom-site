import { json, loadUsers, nameOfToken } from './_lib.js';

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const token = url.searchParams.get('token') || '';
  const users = await loadUsers(env);
  const name = nameOfToken(users, token);
  if (!name) return json(401, { ok: false, error: '令牌无效或已过期' });
  const u = users[name];
  if (u.banned) return json(403, { ok: false, banned: true, error: '账户已被封禁' });
  return json(200, { ok: true, username: name });
}
