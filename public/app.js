"use strict";

/* ---------- 状态 ---------- */
const state = {
  meta: null,
  data: {},          // 品牌key -> 分片json
  brand: "",         // 当前品牌key
  brandName: "",     // 当前品牌显示名
  model: "",         // 当前机型（机型+型号拼接作为 value）
  version: "",       // 当前版本
  searchTimer: null,
};

const $ = (id) => document.getElementById(id);

/* ---------- 工具 ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 2200);
}

function badge(type) {
  const t = String(type || "");
  const isFlash = t.includes("线刷");
  return `<span class="badge ${isFlash ? "flash" : "card"}">${esc(t || "卡刷包")}</span>`;
}

function fmtSize(v) {
  if (!v) return "";
  const n = Number(v);
  if (!isFinite(n) || n <= 0) return String(v);
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let x = n;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(x >= 100 ? 0 : 2)} ${u[i]}`;
}

/* ---------- 数据加载 ---------- */
async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return r.json();
}

async function loadBrand(key) {
  if (state.data[key]) return state.data[key];
  const meta = state.meta.brand || {};
  const info = Object.values(meta).find((m) => m && m.file === key + ".json");
  const file = info ? info.file : key + ".json";
  state.data[key] = await loadJSON(`data/${file}`);
  return state.data[key];
}

/* ---------- 渲染 ---------- */
function renderBrands() {
  const brands = state.meta.brand || {};
  const list = $("brandList");
  list.innerHTML = "";
  const order = ["OPPO", "一加", "真我"];
  order.forEach((name) => {
    const info = brands[name];
    if (!info) return;
    const key = info.file.replace(/\.json$/, "");
    const el = document.createElement("div");
    el.className = "brand-card";
    el.innerHTML = `
      <div class="b-name">${esc(name)}</div>
      <div class="b-stat">${info.机型数} 机型 · ${info.版本数} 版本</div>`;
    el.onclick = () => selectBrand(key, name);
    list.appendChild(el);
  });
  $("footBrands").textContent =
    `当前收录：${Object.values(brands).reduce((s, b) => s + (b.版本数 || 0), 0)} 个版本链接`;
}

async function selectBrand(key, name) {
  state.brand = key;
  state.brandName = name;
  state.model = "";
  state.version = "";
  $("detail").classList.add("hidden");
  $("stepModel").classList.remove("hidden");
  $("stepVersion").classList.add("hidden");
  $("crumbModel").innerHTML = `品牌：<b>${esc(name)}</b>`;
  const sel = $("modelSelect");
  sel.innerHTML = '<option value="">加载机型中…</option>';
  try {
    const d = await loadBrand(key);
    sel.innerHTML = '<option value="">请选择机型…</option>';
    d.机型.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = `${m.机型}\u0001${m.型号}`;
      opt.textContent = `${m.机型}（${m.型号}）· ${m.版本数} 版本`;
      sel.appendChild(opt);
    });
  } catch (e) {
    sel.innerHTML = '<option value="">加载失败，请刷新重试</option>';
    toast("机型加载失败：" + e.message);
  }
  sel.scrollIntoView({ behavior: "smooth", block: "center" });
}

function onModelChange() {
  const sel = $("modelSelect");
  state.model = sel.value;
  state.version = "";
  $("stepVersion").classList.remove("hidden");
  $("detail").classList.add("hidden");
  if (!state.model) {
    $("stepVersion").classList.add("hidden");
    return;
  }
  const [mName, mCode] = state.model.split("\u0001");
  $("crumbVersion").innerHTML =
    `品牌：<b>${esc(state.brandName || "")}</b> · 机型：<b>${esc(mName)}（${esc(mCode)}）</b>`;
  const d = state.data[state.brand];
  const vsel = $("versionSelect");
  vsel.innerHTML = '<option value="">请选择版本…</option>';
  const rows = d.版本.filter(
    (r) => (r.机型 || "") === mName && (r.型号 || "") === mCode
  );
  const seen = new Map();
  rows.forEach((r) => {
    const ver = r.版本 || r.OTA版本 || "";
    if (!ver || seen.has(ver)) return;
    seen.set(ver, true);
    const opt = document.createElement("option");
    opt.value = ver;
    opt.textContent = `${ver} · ${r.类型 || ""}`;
    vsel.appendChild(opt);
  });
  vsel.scrollIntoView({ behavior: "smooth", block: "center" });
}

function onVersionChange() {
  state.version = $("versionSelect").value;
  if (!state.version) {
    $("detail").classList.add("hidden");
    return;
  }
  const [mName, mCode] = state.model.split("\u0001");
  const d = state.data[state.brand];
  const rows = d.版本.filter(
    (r) =>
      (r.机型 || "") === mName &&
      (r.型号 || "") === mCode &&
      ((r.版本 || "") === state.version || (r.OTA版本 || "") === state.version)
  );
  renderDetail(rows);
  $("detail").classList.remove("hidden");
  $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDetail(rows) {
  const box = $("detailList");
  box.innerHTML = "";
  if (!rows.length) {
    box.innerHTML = '<div class="loading">该版本暂无链接记录</div>';
    return;
  }
  rows.forEach((r) => {
    const item = document.createElement("div");
    item.className = "detail-item";
    item.innerHTML = `
      <div class="d-head">
        ${badge(r.类型)}
        <span class="d-ver">${esc(r.版本 || r.OTA版本 || "")}</span>
      </div>
      <div class="d-rows">
        ${r.OTA版本 ? `<div>OTA 版本：<b>${esc(r.OTA版本)}</b></div>` : ""}
        ${r.安全补丁 ? `<div>安全补丁：<b>${esc(r.安全补丁)}</b></div>` : ""}
        ${r.地区 ? `<div>地区：<b>${esc(r.地区)}</b></div>` : ""}
        ${r.来源 ? `<div>数据来源：<b>${esc(r.来源)}</b></div>` : ""}
        <div>链接：<b>${esc(r.链接)}</b></div>
      </div>
      <div class="d-link">
        <button class="btn primary" data-act="copy" data-link="${esc(r.链接)}">复制链接</button>
        <a class="btn" href="${esc(r.链接)}" target="_blank" rel="noopener">打开链接</a>
      </div>`;
    box.appendChild(item);
  });
}

$("detailList").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act=copy]");
  if (!btn) return;
  const link = btn.getAttribute("data-link");
  navigator.clipboard?.writeText(link).then(
    () => toast("链接已复制"),
    () => {
      const ta = document.createElement("textarea");
      ta.value = link;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); toast("链接已复制"); } catch { toast("复制失败，请手动复制"); }
      ta.remove();
    }
  );
});

/* ---------- 搜索 ---------- */
async function doSearch(q) {
  const box = $("searchResults");
  q = q.trim().toLowerCase();
  if (q.length < 2) {
    box.classList.add("hidden");
    return;
  }
  const brandKeys = Object.keys(state.meta.brand || {}).map((name) =>
    state.meta.brand[name].file.replace(/\.json$/, "")
  );
  try {
    await Promise.all(brandKeys.map((k) => loadBrand(k)));
  } catch (e) {
    box.innerHTML = `<div class="sr-empty">数据加载失败：${esc(e.message)}</div>`;
    box.classList.remove("hidden");
    return;
  }
  const hits = [];
  Object.keys(state.data).forEach((key) => {
    const d = state.data[key];
    d.版本.forEach((r) => {
      const text = `${r.机型} ${r.型号} ${r.版本} ${r.OTA版本}`.toLowerCase();
      if (text.includes(q)) hits.push({ key, name: d.品牌, r });
    });
  });
  hits.sort((a, b) => (a.r.机型 + a.r.型号).localeCompare(b.r.机型 + b.r.型号, "zh"));
  const top = hits.slice(0, 60);
  box.innerHTML = top.length
    ? top.map((h) => `
        <div class="sr-item" data-key="${esc(h.key)}" data-model="${esc(h.r.机型)}\u0001${esc(h.r.型号)}" data-ver="${esc(h.r.版本 || h.r.OTA版本)}">
          <div class="sr-name">${esc(h.name)} · ${esc(h.r.机型)}（${esc(h.r.型号)}） ${badge(h.r.类型)}</div>
          <div class="sr-sub">${esc(h.r.版本 || h.r.OTA版本)}${h.r.OTA版本 && h.r.OTA版本 !== h.r.版本 ? " · OTA " + esc(h.r.OTA版本) : ""}</div>
        </div>`).join("")
    : '<div class="sr-empty">没有匹配的结果</div>';
  box.classList.remove("hidden");
}

$("searchResults").addEventListener("click", (e) => {
  const item = e.target.closest(".sr-item");
  if (!item) return;
  $("searchResults").classList.add("hidden");
  $("searchInput").value = "";
  const key = item.dataset.key;
  const model = item.dataset.model.replace(/\u0001/g, "\u0001");
  const ver = item.dataset.ver;
  state.brand = key;
  state.model = model;
  state.version = ver;
  const name = state.data[key].品牌;
  $("crumbModel").innerHTML = `品牌：<b>${esc(name)}</b>`;
  $("stepModel").classList.remove("hidden");
  const msel = $("modelSelect");
  msel.innerHTML = '<option value="">请选择机型…</option>';
  state.data[key].机型.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = `${m.机型}\u0001${m.型号}`;
    opt.textContent = `${m.机型}（${m.型号}）· ${m.版本数} 版本`;
    msel.appendChild(opt);
  });
  msel.value = model;
  $("stepVersion").classList.remove("hidden");
  const [mName, mCode] = model.split("\u0001");
  $("crumbVersion").innerHTML =
    `品牌：<b>${esc(name)}</b> · 机型：<b>${esc(mName)}（${esc(mCode)}）</b>`;
  const vsel = $("versionSelect");
  vsel.innerHTML = '<option value="">请选择版本…</option>';
  const seen = new Set();
  state.data[key].版本
    .filter((r) => (r.机型 || "") === mName && (r.型号 || "") === mCode)
    .forEach((r) => {
      const v = r.版本 || r.OTA版本 || "";
      if (!v || seen.has(v)) return;
      seen.add(v);
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = `${v} · ${r.类型 || ""}`;
      vsel.appendChild(opt);
    });
  vsel.value = ver;
  const rows = state.data[key].版本.filter(
    (r) =>
      (r.机型 || "") === mName &&
      (r.型号 || "") === mCode &&
      ((r.版本 || "") === ver || (r.OTA版本 || "") === ver)
  );
  renderDetail(rows);
  $("detail").classList.remove("hidden");
  $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("searchInput").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  const q = $("searchInput").value;
  state.searchTimer = setTimeout(() => doSearch(q), 260);
});
$("searchInput").addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("searchResults").classList.add("hidden");
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-card")) $("searchResults").classList.add("hidden");
});

$("modelSelect").addEventListener("change", onModelChange);
$("versionSelect").addEventListener("change", onVersionChange);

/* ---------- 初始化 ---------- */
(async function init() {
  try {
    state.meta = await loadJSON("data/meta.json");
    $("updatedAt").textContent = `数据更新时间：${state.meta.生成时间}`;
    renderBrands();
  } catch (e) {
    $("updatedAt").textContent = "数据加载失败，请检查部署或稍后重试";
    $("brandList").innerHTML = `<div class="loading">加载失败：${esc(e.message)}</div>`;
  }
})();
