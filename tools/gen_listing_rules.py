"""
gen_listing_rules.py —— 把共享规则 JSON 转成前端可加载的 JS。

为什么要有这一步:
  前端是纯静态、零构建、双击 index.html 就能跑的。file:// 协议下 fetch() 读本地
  JSON 会被 CORS 拦住,所以不能直接让浏览器去读 shared/listing_rules.json,
  必须生成一个 `window.LISTING_RULES = {...}` 的 .js 文件。

用法:
    python tools/gen_listing_rules.py

改了 shared/listing_rules.json 之后**必须**重跑本脚本,否则两端会再次漂移。
CI / 回归测试里有同步校验(python_backend/tests/test_rules_sync.py),
忘了重跑会直接测试失败。
"""
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
SRC = os.path.join(_ROOT, "shared", "listing_rules.json")
DST = os.path.join(_ROOT, "assets", "js", "listing_rules.js")

HEADER = """/* ==========================================================
 * listing_rules.js —— 【自动生成，请勿手改】
 *
 * 数据源: shared/listing_rules.json
 * 生成器: tools/gen_listing_rules.py
 * 改了 JSON 之后请重跑: python tools/gen_listing_rules.py
 *
 * 这里放的是 Listing 生成与校验规则的唯一数据源,Python 后端
 * (python_backend/listing_gen.py) 加载的是同一个 JSON 文件。
 * 以前两端各硬编码一份,并且已经实际漂移过:前端漏了 best-seller /
 * cure / 100% cure 三个违规词,导致"前端显示校验通过、后端却拦截"。
 * ========================================================== */
"""


def main() -> int:
    if not os.path.exists(SRC):
        print(f"[gen] 找不到数据源: {SRC}", file=sys.stderr)
        return 1

    with io.open(SRC, "r", encoding="utf-8") as f:
        rules = json.load(f)

    body = json.dumps(rules, ensure_ascii=False, indent=2, sort_keys=False)
    out = HEADER + "window.LISTING_RULES = " + body + ";\n"

    # 先写临时文件再替换,避免写一半被打断留下坏文件
    tmp = DST + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, DST)

    print(f"[gen] 已生成 {os.path.relpath(DST, _ROOT)}")
    print(f"[gen] 违规词 {len(rules['banned_words'])} 条 / 类目 {len([k for k in rules['category_schema'] if not k.startswith('_')])} 个 / 词表 {len(rules['cn2en'])} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
