# -*- coding: utf-8 -*-
"""从 OPLUS 标准格式数据库生成网站分片数据。

输出（public/data/）：
  meta.json           生成时间 / 品牌统计
  oppo.json           品牌 OPPO 的机型 + 版本
  oneplus.json        品牌 一加
  realme.json         品牌 真我

用法：python tools/build_site_data.py
"""

import json
import os
import time
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "database", "OPLUS全部版本信息_CN_标准格式.json")
OUT = os.path.join(BASE, "public", "data")

BRAND_KEY = {
    "OPPO": "oppo",
    "一加": "oneplus",
    "真我": "realme",
}

_KEEP = (
    "机型", "型号", "版本", "类型", "OTA版本", "安全补丁",
    "地区", "链接", "来源",
)


def main():
    with open(DB, encoding="utf-8-sig") as f:
        data = json.load(f)
    rows = data.get("结果", data) if isinstance(data, dict) else data

    os.makedirs(OUT, exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    meta = {
        "生成时间": now,
        "说明": "OPLUS(OPPO/一加/真我) 国行 ROM 版本链接库，每天由 OTA 查询自动更新",
        "品牌": {},
        "统计": {"总版本数": len(rows)},
    }

    groups = defaultdict(list)
    for r in rows:
        brand = str(r.get("品牌") or "").strip()
        if brand not in BRAND_KEY:
            continue
        groups[brand].append(r)

    for brand in sorted(groups, key=lambda b: -len(groups[b])):
        brand_rows = groups[brand]
        # 机型汇总（机型+型号，含版本数）
        model_map = defaultdict(int)
        for r in brand_rows:
            key = (str(r.get("机型") or "").strip(), str(r.get("型号") or "").strip())
            model_map[key] += 1
        models = [
            {"机型": k[0], "型号": k[1], "版本数": v}
            for k, v in sorted(
                model_map.items(),
                key=lambda kv: (-kv[1], kv[0][0], kv[0][1]),
            )
        ]
        # 版本明细（精简字段，链接排前方便前端）
        versions = []
        for r in brand_rows:
            v = {k: r.get(k, "") for k in _KEEP}
            v["链接"] = str(v.get("链接") or "").strip()
            if not v["链接"]:
                continue
            versions.append(v)
        versions.sort(
            key=lambda x: (str(x.get("机型")), str(x.get("型号")), str(x.get("版本")))
        )
        key = BRAND_KEY[brand]
        payload = {
            "生成时间": now,
            "品牌": brand,
            "统计": {"机型数": len(models), "版本数": len(versions)},
            "机型": models,
            "版本": versions,
        }
        path = os.path.join(OUT, key + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        meta["品牌"][brand] = {
            "文件": key + ".json",
            "机型数": len(models),
            "版本数": len(versions),
        }
        meta["统计"][brand] = len(versions)
        print(f"{brand}: {len(models)} 机型 / {len(versions)} 版本 -> {key}.json")

    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("meta.json 已生成")
    total = sum(v for k, v in meta["统计"].items() if k != "总版本数")
    print(f"完成：{total} 条链接（源库 {len(rows)} 条）")


if __name__ == "__main__":
    main()
