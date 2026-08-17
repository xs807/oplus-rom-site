// Vercel Serverless Function：ColorOS16 链接重定向（带 userid 头，返回最终签名链接）
export default async function handler(req, res) {
  try {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    const url = typeof body.url === "string" ? body.url.trim() : "";
    if (!/^https:\/\/(component-ota-cn\.allawntech\.com|gauss-compota)/i.test(url)) {
      return res.status(400).json({ ok: false, error: "不支持的链接" });
    }
    const resp = await fetch(url, {
      method: "HEAD",
      headers: { userid: "oplus-ota|" },
      redirect: "manual",
    });
    const status = resp.status;
    const location = resp.headers.get("location") || "";
    if (status === 302 && location) {
      return res.json({ ok: true, status, location });
    }
    if (status === 200) {
      return res.json({ ok: true, status, location: url });
    }
    return res.json({ ok: false, status, error: "未返回重定向链接" });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String((e && e.message) || e) });
  }
}
