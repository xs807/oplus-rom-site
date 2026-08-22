import { json, readBody, listUsers, findUserByToken, getUser, putUser, now, migrateLegacy } from './_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  await migrateLegacy(env);
  const name = await findUserByToken(env, body.token || '');
  if (!name) return json(401, { ok: false, error: '令牌无效' });
  const u = await getUser(env, name);
  if (!u) return json(401, { ok: false, error: '账户不存在' });
  u.banned = true;
  u.ban_reason = '检测到逆向工具（客户端自报）';
  u.banned_at = now();
  await putUser(env, name, u);
  return json(200, { ok: true });
}
