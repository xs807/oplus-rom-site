import { json, readBody, getUser, putUser, hashPassword, isAdminOf, now, migrateLegacy } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  await migrateLegacy(env);
  const admin = await getUser(env, body.admin_user);
  if (!isAdminOf({ [body.admin_user]: admin }, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }
  const name = String(body.username || '').trim();
  const pwd = String(body.password || '');
  if (!name || !pwd) {
    return json(400, { ok: false, error: '用户名和密码不能为空' });
  }
  if (await getUser(env, name)) return json(409, { ok: false, error: '账户已存在' });
  const { salt, hash } = await hashPassword(pwd);
  await putUser(env, name, {
    salt, hash, banned: false, is_admin: !!body.is_admin, created: now(),
  });
  return json(200, { ok: true });
}
