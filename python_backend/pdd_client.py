"""
拼多多开放平台客户端。

签名规则: sign = MD5(client_secret + 按 key 字典序拼接的 "key+value" + client_secret).upper()
参考: https://open.pinduoduo.com/application/document/api

注意: 接口字段和签名细节可能调整,正式接入前请对照官方最新文档核对。

关于嵌套参数(重要):
    pdd.goods.add / pdd.goods.update 这类接口的 goods_commit_info 是**复合字段**,
    官方要求传 JSON 字符串。如果直接把 dict 交给 requests 做 form 编码,
    发出去的是 Python 的 str(dict)(单引号,不是合法 JSON),服务端解析失败;
    而且签名必须用**序列化之后**的字符串来算,否则签名和实际发送内容不一致,
    会直接报 "无效签名"。所以这里统一在 _call() 里先 _flatten() 再签名。
"""
import hashlib
import json
import time

import requests

import config


class PinduoduoClient:
    def __init__(self):
        self.endpoint = config.PDD_API_ENDPOINT

    @staticmethod
    def _flatten(params: dict) -> dict:
        """
        把嵌套的 dict/list 参数序列化成 JSON 字符串,其余原样保留(丢弃 None)。
        签名和发送必须用同一份结果,否则签名校验不过。
        """
        out = {}
        for k, v in params.items():
            if v is None:
                continue
            out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        return out

    def _sign(self, params: dict) -> str:
        params = self._flatten(params)
        sorted_items = sorted(params.items(), key=lambda x: x[0])
        raw = (
            config.PDD_CLIENT_SECRET
            + "".join(f"{k}{v}" for k, v in sorted_items)
            + config.PDD_CLIENT_SECRET
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    def _call(self, api_type: str, biz_params: dict) -> dict:
        params = self._flatten({
            "client_id": config.PDD_CLIENT_ID,
            "access_token": config.PDD_ACCESS_TOKEN,
            "timestamp": str(int(time.time())),
            "data_type": "JSON",
            "type": api_type,
            **biz_params,
        })
        params["sign"] = self._sign(params)
        resp = requests.post(self.endpoint, data=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error_response" in data:
            raise RuntimeError(f"拼多多 API 返回错误: {data['error_response']}")
        return data

    # ---------- 自动上架 / 更新商品 ----------
    def add_goods(self, goods_payload: dict) -> dict:
        """goods_payload 需符合 pdd.goods.add 的字段要求(名称、类目、价格、库存、规格等)"""
        return self._call("pdd.goods.add", {"goods_commit_info": goods_payload})

    def update_goods(self, goods_id: int, goods_payload: dict) -> dict:
        goods_payload = {**goods_payload, "goods_id": goods_id}
        return self._call("pdd.goods.update", {"goods_commit_info": goods_payload})

    # ---------- 监控商品 / 库存 ----------
    def get_goods_list(self, page: int = 1, page_size: int = 100) -> dict:
        """单页查询。批量场景请用 iter_goods_list(),否则只会拿到前 page_size 个商品。"""
        return self._call("pdd.goods.list.get", {"page": page, "page_size": page_size})

    def iter_goods_list(self, page_size: int = 100, max_pages: int = 500) -> list:
        """
        自动翻页拉取全部商品,避免只拿到第一页导致后面的商品从未被监控。
        返回 goods_list 合并后的数组。
        """
        goods, page = [], 1
        while page <= max_pages:
            data = self.get_goods_list(page=page, page_size=page_size)
            resp = data.get("goods_list_get_response") or {}
            batch = resp.get("goods_list") or []
            goods.extend(batch)

            total = resp.get("total_count")
            # 没有 total_count 时,用"本页是否满了"判断是否还有下一页
            has_more = (total is not None and len(goods) < int(total)) or (
                total is None and len(batch) >= page_size
            )
            if not has_more or not batch:
                break
            page += 1

        return goods

    # ---------- 监控订单 ----------
    def get_order_list(self, start_time: int, end_time: int, page: int = 1, page_size: int = 100) -> dict:
        return self._call(
            "pdd.order.list.get",
            {
                "start_updated_at": start_time,
                "end_updated_at": end_time,
                "page": page,
                "page_size": page_size,
            },
        )
