"""
check_image_api.py —— 用真实 Key 打一次生图接口,验证配置对不对。

为什么要有这个脚本:
  生图配置错了不会报"配置错",只会报一句看不懂的接口错误,或者更糟 ——
  成功返回但你没拿到想要的张数(比如对方舟发了 n,它根本不认,静默只出 1 张)。
  这个脚本把**实际会发出去的请求体**和**原始响应**都打出来,一眼就能对上文档。

用法(会真实调用、真实扣费):
    export IMAGE_ENABLED=true
    export IMAGE_API_KEY=你的方舟APIKey
    python tools/check_image_api.py                # 默认出 1 张
    python tools/check_image_api.py --count 4      # 验证组图模式
    python tools/check_image_api.py --style scene  # 验证某个风格
    python tools/check_image_api.py --subject "black wireless earbuds"
    python tools/check_image_api.py --save         # 生完立刻下载落盘(验证转存)

退出码:0 成功,1 失败(配置缺失 / 接口报错 / 没解析出图片)。
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "python_backend"))

import config        # noqa: E402
import image_gen     # noqa: E402
import image_store   # noqa: E402


def _mask(key: str) -> str:
    if not key:
        return "(空)"
    return key[:6] + "*" * max(0, len(key) - 10) + key[-4:] if len(key) > 10 else "***"


def main() -> int:
    ap = argparse.ArgumentParser(description="打一次真实的生图接口,验证配置")
    ap.add_argument("--subject", default="a black wireless earbuds charging case",
                    help="商品主体描述")
    ap.add_argument("--style", default="amazon_main", help="风格模板名")
    ap.add_argument("--count", type=int, default=1, help="出几张")
    ap.add_argument("--points", default="", help="卖点,逗号分隔")
    ap.add_argument("--save", action="store_true",
                    help="生完立刻下载落盘(方舟 URL 只有 24 小时有效期)")
    args = ap.parse_args()

    print("== 解析后的配置 ==")
    print("  provider    :", image_gen.provider())
    print("  endpoint    :", image_gen.endpoint())
    print("  model       :", image_gen.resolved_model() or "(不传)")
    print("  api key     :", _mask(getattr(config, "IMAGE_API_KEY", "") or ""))
    print("  timeout     :", getattr(config, "IMAGE_TIMEOUT", 60), "秒")

    if not image_gen.available():
        print("\n[失败] 生图服务未配置。需要 IMAGE_ENABLED=true + IMAGE_API_KEY"
              " (+ 自定义供应商时还要 IMAGE_API_BASE_URL)。")
        return 1

    try:
        req = image_gen.build_image_request({
            "subject": args.subject,
            "style": args.style,
            "count": args.count,
            "selling_points": [p for p in args.points.split(",") if p.strip()],
        })
    except ValueError as e:
        print(f"\n[失败] 请求参数不合法: {e}")
        return 1

    print("\n== 实际发出的请求体 ==")
    print(json.dumps(image_gen._build_request_body(req), ensure_ascii=False, indent=2))

    print("\n== 调用中 ==")
    res = image_gen.generate(req)

    if not res["ok"]:
        print(f"[失败] {res['error']}")
        return 1

    print(f"[成功] 拿到 {len(res['images'])} 张:")
    for i, u in enumerate(res["images"], 1):
        print(f"  {i}. {u[:120]}{'...' if len(u) > 120 else ''}")

    if image_gen.provider() == "ark":
        print("\n⚠️ 方舟的图片 URL 只有 24 小时有效期,存进图库前必须先下载转存。")

    if args.save:
        print("\n== 转存到本地 ==")
        saved = image_store.store_generated(res)
        print(f"  目录: {saved['dir']}")
        print(f"  成功: {saved['count']} 张")
        for rec in saved["saved"]:
            print(f"    - {rec['name']}  ({rec['bytes']} 字节)")
        for rec in saved["failed"]:
            print(f"    ! 失败: {rec['error']}")
        if not saved["ok"]:
            print("[失败] 一张都没存下来")
            return 1
        print("\n提示:图库请存本地路径,不要存 URL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
