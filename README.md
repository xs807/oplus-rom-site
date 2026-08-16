# OPLUS ROM 查询网站

OPPO / 一加 / 真我 国行 ROM（卡刷包 / 线刷包）版本链接查询网站。

- 前端：纯静态（无框架、无构建步骤），按品牌分片加载数据
- 数据源：`database/OPLUS全部版本信息_CN_标准格式.json`（13,934 条）
- 更新：GitHub Actions 每天自动跑 OTA 查询，新版本合并回数据库并重新生成站点数据
- 部署：一键部署到 Vercel（GitHub 集成自动部署）

## 目录结构

```
├── public/                  # 网站静态文件（Vercel 输出目录）
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── data/                # 按品牌分片数据（meta/oppo/oneplus/realme.json）
├── database/
│   └── OPLUS全部版本信息_CN_标准格式.json   # 主数据库（每日更新）
├── ota/
│   ├── oplus_query.py       # OTA 查询核心（realme-ota 官方接口）
│   ├── update_daily.py      # 每日更新脚本（种子+链式查询、合并去重）
│   └── last_update.json     # 最近一次更新统计（运行后生成）
├── tools/
│   └── build_site_data.py   # 数据库 -> 网站分片数据
├── .github/workflows/daily-update.yml  # 每日自动更新
├── requirements.txt
└── vercel.json
```

## 一键部署到 Vercel

1. 把本项目推送到 GitHub 仓库：
   ```bash
   git init
   git add -A
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/oplus-rom-site.git
   git push -u origin main
   ```
2. 打开 [vercel.com/new](https://vercel.com/new)，导入刚创建的仓库：
   - Framework Preset：选 **Other**（纯静态，无需构建命令）
   - 无需配置任何环境变量
   - 点击 **Deploy**
3. 完成。之后每次 GitHub Actions 更新数据并 push，Vercel 会自动重新部署。

> 国内直连 Vercel 较慢，可在 Vercel 项目 Settings → Domains 绑定自己的域名，
> 或使用 Cloudflare Pages 替代（同样支持 GitHub 自动部署，把输出目录设为 `public` 即可）。

## 每日更新

- GitHub Actions 每天 06:00（北京时间）自动运行 `ota/update_daily.py`
- 逻辑：以数据库已有 OTA 版本为种子，取最新版本链式查询 → 查到新版本继续查，
  查不到换下一个种子版本 → 新版本（同链接/同版本去重）合并回数据库 →
  重新生成 `public/data/*.json` → 自动提交推送 → Vercel 自动重新部署
- 也可在 Actions 页面手动点击 **Run workflow** 立即更新
- 更新统计见 `ota/last_update.json`

## 本地运行

```bash
pip install -r requirements.txt

# 1) 每日更新（查询 OTA 并合并新版本）
python ota/update_daily.py

# 2) 重新生成网站数据
python tools/build_site_data.py

# 3) 本地预览（public 目录即网站内容）
python -m http.server 8000 -d public
```

## 数据说明

- 品牌：OPPO / 一加 / 真我（国行）
- 类型：卡刷包 / 线刷包
- 链接：部分卡刷包（ColorOS16 等）下载时需带 `userid: oplus-ota|` 请求头，
  网页端如遇 403 请使用工具（ROOT 工具 / OTA 查询工具）内下载
