"""Responsibilities: ORS 步行路线调用、容量限制及统一路线契约。
Implementation: 固定 HTTPS 主机、20 秒网络 I/O 超时、1 MiB 响应上限；不重试、不重定向。
Related Modules: core.maps；前端消费 geometry/summary/steps。
Declaration Index: RouteError/RouteError.__init__ — 公开错误；validate_coordinate — 坐标校验；
normalize_route — 上游契约校验；walking_route — 限流并调用 ORS。
Variable Index: ORS_HOST/ORS_PATH — 唯一上游；TIMEOUT_SECONDS/MAX_RESPONSE_BYTES — 传输边界；
MAX_REQUESTS_PER_MINUTE — 单 worker 总额度；route_slots — 两个并发；
request_times/request_lock — 限流状态；
logger — 无 key/坐标日志；ATTRIBUTION — 路由数据署名。
"""

import http.client
import json
import logging
import math
import os
import time
from collections import deque
from threading import BoundedSemaphore, Lock

ORS_HOST = "api.heigit.org"
ORS_PATH = "/openrouteservice/v2/directions/foot-walking/geojson"
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1048576
MAX_REQUESTS_PER_MINUTE = 20
ATTRIBUTION = "© openrouteservice.org by HeiGIT | © OpenStreetMap contributors"
route_slots = BoundedSemaphore(2)
request_times = deque()
request_lock = Lock()
logger = logging.getLogger(__name__)


class RouteError(Exception):
    """Functionality: 传递可公开路线错误，不携带上游正文或凭据。
    Logic: 固定 code/detail/status 供 HTTP 适配层使用。
    Constraints: detail 由本模块生成，禁止放入外部异常字符串。
    """

    def __init__(self, code, detail, status):
        """Functionality: 初始化诊断错误。
        Inputs: self、公开 code/detail 和 HTTP status；Outputs: 异常实例状态。
        Logic: Exception 正文为安全 detail；Constraints: 无日志和网络副作用。
        """
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def validate_coordinate(value):
    """Functionality: 校验一个 [经度, 纬度] 坐标。
    Inputs: JSON value；Outputs: 两个 float 或 ValueError。
    Logic: 排除 bool、非有限数、错误维度与越界值。
    Constraints: 不接受文本数字，不修改位置或推断 IP 坐标。
    """
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("坐标必须为 [经度, 纬度]。")
    for number, limit in zip(value, (180, 90), strict=True):
        if type(number) not in (int, float) or abs(number) > limit or not math.isfinite(number):
            raise ValueError("经纬度必须是范围内的有限数值。")
    return [float(number) for number in value]


def normalize_route(data):
    """Functionality: 将真实 ORS GeoJSON 转为稳定业务契约。
    Inputs: 已解码 ORS JSON；Outputs: geometry/summary/steps/provider/profile/attribution。
    Logic: 验证几何、距离、时长和步骤索引，移除 metadata 中的请求坐标副本。
    Constraints: 异常响应统一 502；不生成替代路线、虚构步骤或默认距离。
    """
    try:
        if data["type"] != "FeatureCollection" or len(data["features"]) != 1:
            raise ValueError
        feature = data["features"][0]
        geometry = feature["geometry"]
        if geometry["type"] != "LineString" or len(geometry["coordinates"]) < 2:
            raise ValueError
        coordinates = [validate_coordinate(point) for point in geometry["coordinates"]]
        properties = feature["properties"]
        summary = properties["summary"]
        steps = []
        for segment in properties["segments"]:
            for step in segment["steps"]:
                if not isinstance(step["instruction"], str) or not step["instruction"].strip():
                    raise ValueError
                points = step["way_points"]
                if (
                    not isinstance(points, list)
                    or len(points) != 2
                    or any(type(point) is not int for point in points)
                    or not 0 <= points[0] <= points[1] < len(coordinates)
                    or type(step["type"]) is not int
                    or not 0 <= step["type"] <= 13
                ):
                    raise ValueError
                steps.append(
                    {
                        "instruction": step["instruction"],
                        "type": step["type"],
                        "distance_m": step["distance"],
                        "duration_s": step["duration"],
                        "way_points": points,
                    }
                )
        values = [summary["distance"], summary["duration"]]
        values.extend(value for step in steps for value in (step["distance_m"], step["duration_s"]))
        if not steps or any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
            for value in values
        ):
            raise ValueError
        return {
            "provider": "openrouteservice",
            "profile": "foot-walking",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "summary": {"distance_m": summary["distance"], "duration_s": summary["duration"]},
            "steps": steps,
            "attribution": ATTRIBUTION,
        }
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise RouteError(
            "ors_invalid_response", "ORS 返回的路线数据不符合接口契约。", 502
        ) from error


def walking_route(start, end, request_id):
    """Functionality: 调用托管 ORS，限制匿名诊断服务的上游资源消耗。
    Inputs: 已校验 start/end、非敏感 request_id、ORS_API_KEY 环境。
    Outputs: 规范路线或 RouteError；日志只含标识、状态、耗时和数量。
    Logic: 单 worker 20 次/分钟、最多两次并发；凭据只置 Authorization；finally 释放连接和槽位。
    Constraints: 单 worker 限流非认证；1 MiB、20 秒网络 I/O 边界；不重试、不重定向、不存坐标。
    """
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise RouteError("ors_not_configured", "服务端尚未配置 ORS_API_KEY。", 503)
    started = time.monotonic()
    with request_lock:
        while request_times and request_times[0] <= started - 60:
            request_times.popleft()
        if len(request_times) >= MAX_REQUESTS_PER_MINUTE:
            raise RouteError(
                "route_rate_limit", "路线调用达到每分钟 20 次上限，请稍后手动尝试。", 429
            )
        if not route_slots.acquire(blocking=False):
            raise RouteError("route_capacity", "已有两次路线请求正在处理，请稍后手动尝试。", 429)
        request_times.append(started)
    connection = None
    try:
        connection = http.client.HTTPSConnection(ORS_HOST, timeout=TIMEOUT_SECONDS)
        logger.info("ORS request started id=%s profile=foot-walking", request_id)
        body = json.dumps({"coordinates": [start, end], "instructions": True, "language": "en"})
        # Functionality: 请求 GeoJSON；Logic: Accept 与路径输出一致，避免 ORS 406。
        # Constraints: Content-Type 描述请求 JSON，Accept 描述返回 GeoJSON。
        connection.request(
            "POST",
            ORS_PATH,
            body=body,
            headers={
                "Authorization": key,
                "Content-Type": "application/json",
                "Accept": "application/geo+json",
                "User-Agent": "PRS-VisionGuide/1.0",
            },
        )
        response = connection.getresponse()
        logger.info("ORS response received id=%s status=%s", request_id, response.status)
        if response.status in (401, 403):
            raise RouteError(
                "ors_authentication_failed", "ORS 拒绝凭据，请检查服务端 key 或账号权限。", 502
            )
        if response.status == 429:
            raise RouteError("ors_quota_exceeded", "ORS 配额已达到上限，请检查账号额度。", 503)
        if response.status in (400, 404, 422):
            raise RouteError("ors_route_unavailable", "ORS 无法生成此步行路线，请检查起终点。", 422)
        if response.status != 200:
            raise RouteError("ors_upstream_error", "ORS 服务返回非成功状态。", 502)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RouteError("ors_response_too_large", "ORS 路线超过本原型的 1 MiB 响应上限。", 502)
        try:
            route = normalize_route(json.loads(raw))
        except (ValueError, UnicodeDecodeError) as error:
            raise RouteError("ors_invalid_response", "ORS 响应无法解码为 JSON。", 502) from error
        logger.info(
            "ORS request completed id=%s elapsed_ms=%d points=%d steps=%d",
            request_id,
            (time.monotonic() - started) * 1000,
            len(route["geometry"]["coordinates"]),
            len(route["steps"]),
        )
        return route
    except TimeoutError as error:
        raise RouteError("ors_timeout", "ORS 网络 I/O 超时，请检查网络后手动尝试。", 504) from error
    except (OSError, http.client.HTTPException) as error:
        logger.warning(
            "ORS transport failed id=%s exception_type=%s", request_id, type(error).__name__
        )
        raise RouteError(
            "ors_network_error", "无法连接 ORS，请检查服务器网络或 TLS。", 502
        ) from error
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            route_slots.release()
