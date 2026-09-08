"""
亚马逊 SP-API 客户端。

鉴权分两步:
1. 用 refresh_token 向 LWA(Login with Amazon)换取短期 access_token
2. 用 AWS SigV4 给每个请求签名(SP-API 要求)

关于大数据量的说明(重要):
- getInventorySummaries **没有 pageSize 参数**,服务端按页返回(通常 50 条/页),
  必须用响应里的 payload.pagination.nextToken 翻页,否则只会拿到第一页 ——
  不报错,只会静默漏数据。
- 限流(rate 2 req/s, burst 2):翻页时需要在请求之间留间隔,遇到 429 要按 Retry-After 退避。
- 真正省请求的是 startDateTime **增量拉取**:只返回该时间点之后有变更的库存,
  通常能把上千次翻页降到几十次。注意:传 startDateTime 时 sellerSkus/sellerSku 会被忽略,
  且官方要求 startDateTime 与 nextToken 一起传。
- SKU 量级很大时(十万以上),全量快照建议改走 Reports API(异步生成报告),
  一次报告可替代数千次调用。

注意: SP-API 的接口路径、限流数值会不定期调整,正式接入前请对照官方最新文档核对:
https://developer-docs.amazon.com/sp-api/
"""
import logging
import time

import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

import config

log = logging.getLogger(__name__)

# 官方默认限流(每秒请求数)。实际配额以响应头 x-amzn-RateLimit-Limit 为准,
# 业务量大的卖家可能拿到更高的 rate/burst。
RATE_INVENTORY = 2.0      # getInventorySummaries: rate 2, burst 2
RATE_ORDERS = 0.0167      # getOrders: rate 0.0167(约 1 次/分钟), burst 20


class AmazonSPAPIClient:
    def __init__(self):
        self._access_token = None
        self._token_expiry = 0

    # ---------------- 鉴权 ----------------
    def _get_lwa_token(self) -> str:
        """用 refresh_token 换取 access_token,提前 60 秒刷新避免过期"""
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        resp = requests.post(
            "https://api.amazon.com/auth/o2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": config.AMAZON_REFRESH_TOKEN,
                "client_id": config.AMAZON_CLIENT_ID,
                "client_secret": config.AMAZON_CLIENT_SECRET,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._access_token

    # ---------------- 请求 + 限流退避 ----------------
    def _signed_request(self, method: str, path: str, params: dict = None, body: dict = None,
                        max_retries: int = 5):
        """
        带重试的签名请求:
        - 429 (Too Many Requests):按 Retry-After 退避,没有该头就指数退避
        - 5xx:指数退避
        避免一次限流就把整轮监控打断。
        """
        last_exc = None
        for attempt in range(max_retries):
            access_token = self._get_lwa_token()
            url = f"{config.AMAZON_ENDPOINT}{path}"

            request = AWSRequest(
                method=method, url=url, data=body, params=params or {},
                headers={
                    "x-amz-access-token": access_token,
                    "content-type": "application/json",
                },
            )
            credentials = Credentials(
                access_key=config.AMAZON_AWS_ACCESS_KEY,
                secret_key=config.AMAZON_AWS_SECRET_KEY,
            )
            SigV4Auth(credentials, "execute-api", config.AMAZON_REGION).add_auth(request)
            prepared = request.prepare()

            resp = requests.request(
                method, prepared.url, headers=dict(prepared.headers), data=body, timeout=30
            )

            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After")
                wait = float(wait) if wait else min(2 ** attempt, 60)
                log.warning("触发限流(429),等待 %.1fs 后重试 (%d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                last_exc = RuntimeError(f"429 Too Many Requests: {path}")
                continue

            if 500 <= resp.status_code < 600:
                wait = min(2 ** attempt, 60)
                log.warning("服务端错误(%d),等待 %.1fs 后重试", resp.status_code, wait)
                time.sleep(wait)
                last_exc = RuntimeError(f"{resp.status_code} from {path}")
                continue

            resp.raise_for_status()
            return resp.json()

        raise last_exc or RuntimeError(f"请求失败: {path}")

    @staticmethod
    def _pace(rate: float):
        """按限流速率节流:保证请求间隔不小于 1/rate 秒"""
        time.sleep(1.0 / rate if rate > 0 else 0)

    # ---------------- 自动上架 / 更新商品 ----------------
    def create_or_update_listing(self, sku: str, listing_payload: dict) -> dict:
        """
        listing_payload 需要符合 Listings Items API 中该商品类目 schema 的字段要求
        (标题、图片、价格、库存、变体属性等),不同类目字段不同,建议先用
        Product Type Definitions API 拉取对应类目的 schema 再组装。
        """
        path = f"/listings/2021-08-01/items/{config.AMAZON_SELLER_ID}/{sku}"
        params = {"marketplaceIds": config.AMAZON_MARKETPLACE_ID}
        return self._signed_request("PUT", path, params=params, body=listing_payload)

    # ---------------- 监控库存(分页 + 增量) ----------------
    def get_inventory_summaries(self, start_datetime: str = None, seller_skus: list = None,
                                max_pages: int = 2000) -> list:
        """
        返回**全部**库存摘要列表(已自动翻页),而不是只返回第一页。

        :param start_datetime: ISO8601 时间。传了就只返回该时间之后**有变更**的库存(增量拉取),
                               能把上千次翻页降到几十次;不传则全量。
                               注意:传 startDateTime 时 sellerSkus / sellerSku 参数会被忽略。
        :param seller_skus:    只查指定 SKU(单次最多 50 个),用于重点 SKU 高频监控。
        :param max_pages:      翻页上限,防止异常情况下无限循环。
        :return: list[dict],每个元素是一个 SKU 的库存摘要。
        """
        path = "/fba/inventory/v1/summaries"
        base_params = {
            "marketplaceIds": config.AMAZON_MARKETPLACE_ID,
            "granularityType": "Marketplace",
            "granularityId": config.AMAZON_MARKETPLACE_ID,
        }
        if start_datetime:
            # 官方要求:startDateTime 与 nextToken 一起传,否则可能报错
            base_params["startDateTime"] = start_datetime
        elif seller_skus:
            base_params["sellerSkus"] = ",".join(seller_skus[:50])

        summaries, token, pages = [], None, 0
        while pages < max_pages:
            params = dict(base_params)
            if token:
                params["nextToken"] = token

            data = self._signed_request("GET", path, params=params)
            payload = data.get("payload") or {}
            summaries.extend(payload.get("inventorySummaries") or [])

            token = (data.get("pagination") or {}).get("nextToken")
            pages += 1
            if not token:
                break
            self._pace(RATE_INVENTORY)  # 翻页之间按 2 req/s 节流

        if pages >= max_pages:
            log.warning("库存翻页达到上限 %d 页,可能存在未拉取完的数据", max_pages)
        return summaries

    # ---------------- 监控订单(分页 + 限流) ----------------
    def get_recent_orders(self, created_after_iso: str, max_pages: int = 20) -> list:
        """
        返回指定时间之后创建的订单列表(已自动翻页)。

        注意两点:
        1. getOrders 限流极低(rate 0.0167 req/s,约 1 次/分钟,burst 20),
           所以这里翻页很克制,外层也不要高频调用 —— 一小时一次是安全的。
        2. 单页有返回条数上限,大促时一小时的订单可能超过一页,
           不翻页会导致订单数被少算,进而**误触发**"订单量暴跌"告警。
        """
        path = "/orders/v0/orders"
        base_params = {
            "MarketplaceIds": config.AMAZON_MARKETPLACE_ID,
            "CreatedAfter": created_after_iso,
        }

        orders, token, pages = [], None, 0
        while pages < max_pages:
            params = dict(base_params)
            if token:
                params["NextToken"] = token

            data = self._signed_request("GET", path, params=params)
            payload = data.get("payload") or {}
            orders.extend(payload.get("Orders") or [])

            token = payload.get("NextToken")
            pages += 1
            if not token:
                break
            self._pace(1.0)  # 订单接口限流极低,翻页务必放慢

        if pages >= max_pages:
            log.warning("订单翻页达到上限 %d 页,订单数可能被低估", max_pages)
        return orders

    # ---------------- 全量快照:走 Reports API ----------------
    def create_inventory_report(self) -> str:
        """
        SKU 量级很大时(十万以上),全量对账建议用 Reports API 代替逐页翻 API:
        一次报告可替代数千次调用,代价是异步生成(通常几分钟到十几分钟)。

        返回 reportId,之后轮询 get_report() 直到 DONE,再取 reportDocumentId 下载。
        常用 reportType:
          GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA  FBA 可售库存
          GET_MERCHANT_LISTINGS_ALL_DATA           全部在售 Listing
        """
        path = "/reports/2021-06-30/reports"
        body = {
            "reportType": "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA",
            "marketplaceIds": [config.AMAZON_MARKETPLACE_ID],
        }
        data = self._signed_request("POST", path, body=body)
        return data.get("reportId")

    def get_report(self, report_id: str) -> dict:
        """查询报告生成状态,processingStatus 为 DONE 时可取 reportDocumentId"""
        path = f"/reports/2021-06-30/reports/{report_id}"
        return self._signed_request("GET", path)
