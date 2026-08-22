import { json, readBody, getUser, putUser, isAdminOf, now, migrateLegacy } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  await migrateLegacy(env);
  const admin = await getUser(env, body.admin_user);
  if (!isAdminOf({ [body.admin_user]: admin }, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }
  const name = String(body.username || '').trim();
  const u = await getUser(env, name);
  if (!u) return json(404, { ok: false, error: '账户不存在' });
  u.banned = true;
  u.ban_reason = String(body.reason || '管理员封禁');
  u.banned_at = now();
  await putUser(env, name, u);
  return json(200, { ok: true });
}
