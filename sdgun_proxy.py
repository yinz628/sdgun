# -*- coding: utf-8 -*-
"""
SDGun API 轻量级 CORS 代理(FastAPI + requests)

解决三个浏览器端问题:
  1. CORS 跨域:默认所有响应加 Access-Control-Allow-Origin: *(本地开发;
     设置 SDGUN_CORS_ORIGINS 后收紧为白名单精确回显)
  2. 混合内容:聊天服务/部分图片是 http 明文,HTTPS 页面无法直接访问,由代理转发
  3. 请求指纹:上游 WAF 会校验请求头顺序等指纹(httpx 的默认头序会被判定
     "登录信息已过期",requests 的头序可用)——因此转发层必须用 requests,
     且依赖版本已锁定(deploy/requirements.txt),升级前必须跑 tests/

访问控制(可选,未配置时行为与历史版本完全一致):
  SDGUN_ACCESS_TOKEN  访问令牌;设置后除健康探针/入口页/静态资源外全部要求令牌
  SDGUN_CORS_ORIGINS  CORS 白名单(逗号分隔 origin);未设置时维持 *

路由设计(挂在根路径,上游 302 的相对 Location 会自动落回代理,图片链路天然可用):
  ANY /mag/**   /core/**  /public/**  →  https://mag1.sdgun.net/**
  ANY /msg/**                          →  http://shuidan.chat.magcloud.net/**
  ANY /proxy?url=<完整URL>             →  白名单内的任意目标(picapp/bbs 等图片)

App 头自动补充:User-Agent(MAGAPPX) / mag-version / X-Requested-With / Referer。
会话 Cookie 来源(优先级从高到低):
  1. 前端每次请求带 X-Sdgun-Cookie 头(前端自己管理登录态)
  2. SessionStore(sdgun_session.py,自动吸收上游 Set-Cookie)

模块划分:
  sdgun_session.py  上游身份常量 + 会话存储/自检/抓包解析(纯逻辑,可单测)
  sdgun_sign.py     每日签到(守护线程 + /sign 路由)
  sdgun_watch.py    关注关键字监控(守护线程轮询信息流 + /watch 路由)
  sdgun_proxy.py    本文件:FastAPI 应用、转发核心、其余路由、静态托管

运行:
  python -m uvicorn sdgun_proxy:app --host 127.0.0.1 --port 8000
"""

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import requests as upstream_lib
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

import sdgun_favorites
import sdgun_likes
import sdgun_messages
import sdgun_sign
import sdgun_watch
from sdgun_session import (
    APP_HEADERS,
    AUTH_COOKIE_NAME,
    CHAT_ORIGIN,
    MAIN_ORIGIN,
    DATA_DIR,
    call_upstream,
    find_login_record,
    store,
    upstream_client,
    UPSTREAM_TIMEOUT,
)

if not logging.getLogger().handlers:   # 允许宿主(uvicorn/测试)自带日志配置
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
logger = logging.getLogger("sdgun.proxy")

# ============================ 转发配置 ============================

# 上游响应头里不应透传给浏览器的(httpx/requests 已自动解压,长度必须重算)
STRIP_RESP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length", "set-cookie",
}

ALLOWED_PROXY_HOSTS = {
    "mag1.sdgun.net",
    "shuidan.chat.magcloud.net",
    "picapp.sdgun.net",
    "bbs.sdgun.com.cn",
    "pic.magcloud.cc",
    "sdgun.ymgames.com.cn",   # 带水印的老图(抓包中出现过)
    "shuidan.app1.magcloud.net",  # 版块图标等静态图
}

# HTML 里需要走代理的图床域名(用于 WAP 页图片改写;不含 mag1,
# 避免误改 <script src> 等静态资源)
_IMAGE_HOST_RE = (
    r"(?:picapp\.sdgun\.net|bbs\.sdgun\.com\.cn|pic\.magcloud\.cc"
    r"|sdgun\.ymgames\.com\.cn|shuidan\.app1\.magcloud\.net)"
)


def _rewrite_html_images(html: str) -> str:
    r"""把 HTML(含 var row JSON 字符串里的转义形态)中直连图床的
    src/data-original/poster 改写为 /proxy?url=…,使 WAP 页图片/视频封面可加载。

    兼容形态:
      src="http://picapp.sdgun.net/x.jpg"                     普通 HTML
      data-original="…"(jquery.lazyload 空占位 src)
      src=\"http:\/\/picapp.sdgun.net\/x.jpg\"                内嵌 JSON(转义引号+斜杠)
      //picapp.sdgun.net/…                                    协议相对(改写时补 https)
    URL 片允许含反斜杠(JSON 的 \/),结束于引号前的转义反斜杠会保留。

    双实现提示:图片改写在前端另有一份(webui/src/utils/media.ts 的
    prepareCommentHtml),上游改格式时两处必须同步。
    """
    import re
    from urllib.parse import quote

    pattern = re.compile(
        r'(src|data-original|poster)=(\\?)(["\'])((?:https?:)?\\?/\\?/' + _IMAGE_HOST_RE + r'[^"]*)\3',
        re.IGNORECASE,
    )

    def to_proxy(m: "re.Match[str]") -> str:
        attr, esc, quote_ch, url = m.group(1), m.group(2), m.group(3), m.group(4)
        if "/proxy?url=" in url:
            return m.group(0)
        trailing_bs = url.endswith("\\")
        clean = url.rstrip("\\").replace("\\/", "/")
        if not clean:
            return m.group(0)
        if clean.startswith("//"):
            clean = "https:" + clean   # 协议相对需补 scheme,否则 /proxy 白名单按非 http(s) 拒绝
        new_url = f"/proxy?url={quote(clean, safe='')}"
        if trailing_bs:
            new_url += "\\"
        return f"{attr}={esc}{quote_ch}{new_url}{quote_ch}"

    return pattern.sub(to_proxy, html)


# ============================ 应用与生命周期 ============================

# 上游客户端:共用 sdgun_session.upstream_client(带连接级重试 + 放大连接池;
# 会话锁已收窄到 cookie 读写,转发本身可并发)
upstream = upstream_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 会话自检放 startup(并发探测,见 SessionStore.init_from_disk):
    # import 保持零网络零阻塞,离线可测;会话全失效时启动等待也收敛到 ~15s
    await run_in_threadpool(store.init_from_disk)
    sdgun_sign.start_auto_sign()
    sdgun_watch.start_watcher()
    yield


app = FastAPI(title="SDGun CORS Proxy", docs_url=None, redoc_url=None,
              lifespan=lifespan)

app.include_router(sdgun_sign.router)
app.include_router(sdgun_messages.router)
app.include_router(sdgun_likes.router)
app.include_router(sdgun_favorites.router)
app.include_router(sdgun_watch.router)

# ============================ 访问控制(可选,默认关闭) ============================

# 访问令牌门禁与 CORS 白名单都从环境变量读取,且在**每次请求时读取**而非 import
# 时求值:
#   1. 容器可用 `docker run -e` 在启动后注入,无需改代码/重建镜像;
#   2. 测试可 monkeypatch 在 import 之后逐用例开关,互不污染。
# 未配置时行为与历史版本完全一致(无门禁、CORS 全开)——向后兼容是硬约束。


def _access_token() -> str:
    """当前生效的访问令牌;空串表示门禁关闭。"""
    return os.environ.get("SDGUN_ACCESS_TOKEN", "").strip()


def _cors_origins() -> list:
    """CORS 白名单(去掉末尾斜杠);空列表表示维持 * 全开。"""
    return [o.strip().rstrip("/") for o in os.environ.get("SDGUN_CORS_ORIGINS", "").split(",") if o.strip()]


# 门禁豁免:健康探针/排障入口不泄业务数据(Docker HEALTHCHECK 也依赖);
# 入口页与静态资源必须先能加载,否则 SPA 自身起不来、令牌输入框也无从弹起。
# 只做精确匹配与前缀匹配——/link、/proxy 等动态路由绝不能落进前缀豁免。
_EXEMPT_EXACT = {"/", "/index.html", "/favicon.ico", "/health", "/proxy-info"}
_EXEMPT_PREFIXES = ("/assets/",)


def _is_exempt_path(path: str) -> bool:
    return path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIXES)


def _provided_tokens(request: Request):
    """按两种约定产出请求方提交的令牌原值(Bearer 与 X-Sdgun-Token 任一即可)。"""
    auth = request.headers.get("authorization") or ""
    if auth[:7].lower() == "bearer ":
        yield auth[7:].strip()
    x_token = request.headers.get("x-sdgun-token")
    if x_token:
        yield x_token.strip()


@app.middleware("http")
async def access_token_middleware(request: Request, call_next):
    """可选的访问令牌门禁:SDGUN_ACCESS_TOKEN 为空时完全关闭(直接放行)。

    必须注册在 CORS 中间件之前(Starlette 后注册者在外层):这样本中间件直接
    返回的 401 仍会经过 cors_middleware 补 CORS 头——开发模式下浏览器才能读到
    401 响应体,前端据此弹出令牌输入框;预检 OPTIONS 也在外层短路、无需令牌。
    """
    token = _access_token()
    if not token or _is_exempt_path(request.url.path):
        return await call_next(request)

    expected = token.encode("utf-8")
    # compare_digest 定长比较,避免按字节提前返回的时序侧信道;令牌大小写敏感、
    # 按原值比较。
    if any(hmac.compare_digest(expected, got.encode("utf-8"))
           for got in _provided_tokens(request)):
        return await call_next(request)

    # 只记方法与路径,绝不打印令牌/请求头(日志会被部署脚本回显到终端)
    logger.warning("[auth] 未授权请求: %s %s", request.method, request.url.path)
    return JSONResponse({"success": False, "code": 401, "msg": "未授权:需要访问令牌"},
                        status_code=401)


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


def _cors_headers(origin: Optional[str]) -> dict:
    """按运行期配置计算本次响应的 CORS 头。

    未配置 SDGUN_CORS_ORIGINS → 维持 * 全开(本地开发不变);配置后仅当请求
    Origin 命中白名单时精确回显该 origin,未命中/无 Origin 则一个 CORS 头都不回。
    """
    headers = dict(CORS_HEADERS)
    allowed = _cors_origins()
    if not allowed:
        return headers
    if origin and origin.rstrip("/") in allowed:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"   # 回显型 ACAO 必须声明随 Origin 变化,防缓存串味
        return headers
    return {}


@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    """附加 CORS 头;OPTIONS 预检直接 204 短路(预检不带自定义头,无需令牌)。"""
    cors = _cors_headers(request.headers.get("origin"))
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors)
    response = await call_next(request)
    for k, v in cors.items():
        response.headers[k] = v
    return response


# 入口页必须禁掉浏览器的"启发式缓存":index.html 由 StaticFiles 直出(带
# ETag/Last-Modified 但没有 Cache-Control),浏览器会自行推算一个保鲜期,于是
# 前端更新后用户可能长时间停在旧页面、引着已被删除的旧 assets(2026-10-22 上线
# 实测:"看不到关注栏"就是旧 index.html 仍在本地缓存)。
# 只针对入口页:no-cache 不是 no-store——允许存储但强制每次带 ETag/Last-Modified
# 回源校验,内容没变时 StaticFiles 回 304,代价极低;带哈希的 /assets/** 是内容
# 寻址(文件名即版本),保持现状不动,否则每次访问都要多一轮校验。
_ENTRY_PATHS = {"/", "/index.html"}


@app.middleware("http")
async def entry_no_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in _ENTRY_PATHS:
        response.headers["Cache-Control"] = "no-cache"
    return response


# ============================ 转发实现 ============================

async def forward(request: Request, url: str, keep_query: bool = True,
                  follow_redirects: bool = False) -> Response:
    """把前端请求转发到 url:方法/查询串/表单体保留,头重建为 App 身份。

    注意:上游 HTTP 必须用 requests(其请求头顺序可通过 WAF 指纹校验),
    阻塞调用放线程池,避免卡住事件循环。
    keep_query=False 用于 /proxy?url= 场景:目标 URL 自带查询串,
    不能把代理的 url= 参数再追加进去(会把七牛 imageView2 等参数搞坏)。
    follow_redirects=True 用于 /proxy 图片场景(bbs avatar.php 会 302 到真实图)。
    HTML 响应会做图床 URL 改写(WAP 页图片直连图床,浏览器不可达),
    改写在线程池里执行,避免大页面的正则阻塞事件循环。

    媒体流式(/proxy 路径):视频等大文件按块转发,并透传浏览器的 Range 头
    (上游返回 206/Content-Range 原样回传)——<video> 才能快速起播、拖进度条;
    服务端也不必把整个视频(可达几十 MB)缓冲进内存。
    HTML 类响应仍走缓冲改写路径。

    并发模型:上游请求本身不加全局锁(锁会串行化整站,一张慢图拖住所有请求),
    仅会话 Cookie 的读写由 SessionStore 内部锁保护。
    """
    headers = dict(APP_HEADERS)
    if "content-type" in request.headers:
        headers["Content-Type"] = request.headers["content-type"]

    # Cookie 优先级:前端显式指定 > 代理自维护会话
    cookie = request.headers.get("x-sdgun-cookie") or store.cookie_header()
    if cookie:
        headers["Cookie"] = cookie

    if keep_query and request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()

    # /proxy 承载图片与视频:一律走流式,响应阶段再按 content-type 分流
    # (HTML 仍缓冲改写;媒体按块透传)。identity 禁压缩,保证 content-length 如实。
    stream_mode = request.url.path == "/proxy"

    def do_upstream():
        req_headers = dict(headers)
        if stream_mode:
            req_headers["Accept-Encoding"] = "identity"
            range_header = request.headers.get("range")
            if range_header:
                req_headers["Range"] = range_header   # <video> 起播/拖进度条依赖
        try:
            resp = upstream.request(request.method, url, headers=req_headers, data=body,
                                    timeout=UPSTREAM_TIMEOUT, allow_redirects=follow_redirects,
                                    stream=stream_mode)
        except upstream_lib.RequestException as exc:
            return None, None, f"{type(exc).__name__}: {exc}"
        store.absorb(resp)
        # Cookie 唯一事实来源是 SessionStore;清掉 Session 自身 jar 杜绝双重簿记
        # (实测 requests 2.32.5 显式 Cookie 头优先,清 jar 是把不变式固定下来)
        upstream.cookies.clear()

        content_type = (resp.headers.get("content-type") or "").lower()
        if stream_mode and "text/html" not in content_type and not resp.headers.get("content-encoding"):
            return resp, None, None   # 纯媒体:留给 StreamingResponse 按块透传
        # HTML(或带压缩编码的流式响应):整块读取并按需改写
        content = resp.content
        if "text/html" in content_type:
            try:
                content = _rewrite_html_images(content.decode("utf-8")).encode("utf-8")
            except (UnicodeDecodeError, ValueError):
                pass  # 非 UTF-8 的 HTML 原样返回
        # 私信归档钩子:unReadMsg 拉取即清(附录 8),经过代理的每条消息先落本地库,
        # "历史记录"才能从部署起累积;解析失败静默,绝不影响转发本身
        if content and request.url.path.startswith("/msg/"):
            try:
                sdgun_messages.absorb_upstream(request.url.path, content, sdgun_messages.store)
            except Exception as exc:  # noqa: BLE001 —— 归档失败只告警
                logger.warning("[messages] 归档钩子异常: %s", exc)
        return resp, content, None

    resp, content, err = await run_in_threadpool(do_upstream)
    if err:
        return JSONResponse({"proxy_error": err, "upstream": url}, status_code=502)

    if content is None:   # 媒体流式透传
        passthrough = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in STRIP_RESP_HEADERS | {"content-length"}
        }
        # chunked 上游响应(重定向后的最终响应等)没有 content-length,此时不能
        # 直接下标——KeyError 会让本应 502/206 的请求裸奔成 500。缺头时交给
        # StreamingResponse 自动按 chunked 编码回传
        if (cl := resp.headers.get("content-length")) is not None:
            passthrough["Content-Length"] = cl

        def media_iter():
            try:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                resp.close()

        return StreamingResponse(media_iter(), status_code=resp.status_code,
                                 headers=passthrough)

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in STRIP_RESP_HEADERS
    }
    return Response(content=content,
                    status_code=resp.status_code,
                    headers=resp_headers)


# ============================ 路由 ============================

ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@app.post("/auth/replay-exact")
async def auth_replay_exact():
    """按抓包原样重放登录(含当时的 mag-ss-key / Cookie / UA)。

    ⚠️ 实验性:密码密文带防重放要素,失败会计入"密码错误"计数(3 次锁定账号登录)。
    成功则把新签发的 Cookie 吸收进代理会话并持久化。

    这是会话失效后用户恢复的唯一入口,自身必须有完整的错误处理
    (上游慢/断连时返回结构化 502,而不是让异常裸奔成 500)。
    """
    rec = find_login_record(DATA_DIR)
    if not rec:
        return JSONResponse({"success": False, "msg": "抓包文件里没有登录请求"}, status_code=404)

    def do():
        try:
            resp = upstream_client.post(rec["url"], headers=rec["headers"],
                                        data=rec["body"].encode("utf-8"),
                                        timeout=UPSTREAM_TIMEOUT, allow_redirects=False)
        except upstream_lib.RequestException as exc:
            return None, f"{type(exc).__name__}: {exc}"
        store.absorb(resp)
        return resp, None

    resp, err = await run_in_threadpool(do)
    if err:
        logger.warning("[auth] 登录重放请求失败: %s", err)
        return JSONResponse({"success": False, "msg": f"重放请求失败: {err}"},
                            status_code=502)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"success": False, "msg": resp.text[:200]}
    if payload.get("success") and (payload.get("data") or {}).get("token"):
        store.set(AUTH_COOKIE_NAME, payload["data"]["token"])
    return JSONResponse({"http_status": resp.status_code,
                         "upstream": payload,
                         "session_updated": bool(payload.get("success"))})


@app.post("/auth/login")
async def auth_login(request: Request):
    """账号密码登录(WAP 通道,表单明文,2026-09-09 发现)。

    端点是大写 User 的 /mag/user/v1/User/login——WAP 站 Vue 登录页
    (app.sdgun.com.cn /mag/wap/v1/wap/waphome/login,登录分包 chunk 27)所用,
    与 App 原生通道的小写 user/login(RSA 密文 + mag-ss-key,公钥不外发)不同,
    这条通道 password 为明文表单,无 mag-ss-key,实测 requests 指纹可过。

    ⚠️ 上游失败 3 次临时锁定登录,锁定计数信息在上游 msg 里(如"当前已失败 1/3 次"),
    必须原样透传给前端;本端点绝不自动重试。

    响应 data 含手机号与多枚 token(union/chat/cloud),敏感,只回传安全字段。
    """
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"success": False, "msg": "请求体必须是 JSON"}, status_code=400)
    account = str(payload.get("account") or "").strip()
    password = str(payload.get("password") or "")
    if not account or not password:
        return JSONResponse({"success": False, "msg": "账号和密码不能为空"}, status_code=400)

    # 与 WAP 页一致的表单字段(空串占位字段原样带上)
    form = {"account": account, "password": password,
            "phone": "", "code": "", "security_code": "", "country_code": ""}

    def do():
        try:
            resp = call_upstream(
                "POST", MAIN_ORIGIN + "/mag/user/v1/User/login",
                data=form, allow_redirects=False)
        except upstream_lib.RequestException as exc:
            return None, f"{type(exc).__name__}: {exc}"
        return resp, None

    resp, err = await run_in_threadpool(do)
    if err:
        logger.warning("[auth] 账号密码登录请求失败: %s", err)
        return JSONResponse({"success": False, "msg": f"登录请求失败: {err}"}, status_code=502)
    try:
        up = resp.json()
    except ValueError:
        return JSONResponse({"success": False,
                             "msg": "上游返回非 JSON(疑似被 WAF 拦截)",
                             "http_status": resp.status_code}, status_code=502)

    if not up.get("success"):
        # 失败 msg 带锁定计数,原样透传;不能在这里重试
        return JSONResponse({"success": False, "msg": up.get("msg") or "账号或密码错误"})

    data = up.get("data") or {}
    token = data.get("token")
    if token:
        store.set(AUTH_COOKIE_NAME, token)

    # asyn_ask:登录成功后同步旧论坛(bbs)会话,签名全在 URL 上,GET 无 Cookie 触发
    def sync_bbs():
        for url in (up.get("asyn_ask") or [])[:3]:
            try:
                upstream_client.get(url, headers=dict(APP_HEADERS), timeout=UPSTREAM_TIMEOUT)
            except upstream_lib.RequestException:  # noqa: BLE001 —— 同步失败不影响登录结果
                pass
    await run_in_threadpool(sync_bbs)

    safe_user = {k: data.get(k) for k in ("user_id", "name", "head", "group_name", "group_id")}
    logger.info("[auth] 账号密码登录成功: uid %s (name %s)", data.get("user_id"), data.get("name"))
    return JSONResponse({"success": True, "user": safe_user, "session_updated": True})


@app.get("/proxy-info")
async def index():
    return {
        "proxy": "SDGun CORS Proxy (requests-based)",
        "session_cookie_names": list(store.snapshot().keys()),
        "routes": {
            "主站 API/WAP": "/mag/** /core/** /public/**  → mag1.sdgun.net(HTML 图片自动改写)",
            "聊天服务": "/msg/**  → shuidan.chat.magcloud.net",
            "通用白名单代理": "/proxy?url=<encoded-url>(图片等,跟随重定向)",
            "外链跳桥": "/link?url=<encoded-url>(无 Cookie,绕开证书不匹配短链)",
            "账号密码登录": "POST /auth/login {account, password}(WAP 通道,明文表单)",
            "一键登录(重放)": "POST /auth/replay-exact",
            "签到": "GET /sign/status · POST /sign/run",
            "关注关键字监控": "GET /watch/status|hits|rules · POST /watch/rules[/remove|/toggle|/run]"
                              "(本地监控线程,新帖标题命中关键字即提醒)",
            "健康检查": "GET /health(60s 缓存的会话探活,?force=1 强制刷新)",
        },
        "cookie_source": "X-Sdgun-Cookie 头 > sdgun_session.json(自动吸收 Set-Cookie)",
        "webui": "存在 webui/dist 时由本服务托管页面(http://127.0.0.1:8000/)",
    }


# 会话健康探针(60s 缓存):给 Docker HEALTHCHECK/前端/人排障用。
# 上游改版或会话失效从"被动发现"变"可主动查询"。
_health_cache = {"checked_at": 0.0, "session_ok": False}
_HEALTH_TTL_SECONDS = 60


@app.get("/health")
async def health(force: int = 0):
    now = time.time()
    if force or now - _health_cache["checked_at"] > _HEALTH_TTL_SECONDS:
        ok = await run_in_threadpool(store.validate)
        _health_cache.update(checked_at=now, session_ok=ok)
    return {
        "status": "ok" if _health_cache["session_ok"] else "degraded",
        "session_ok": _health_cache["session_ok"],
        "checked_at": _health_cache["checked_at"],
    }


@app.api_route("/mag/{path:path}", methods=ALL_METHODS)
@app.api_route("/core/{path:path}", methods=ALL_METHODS)
@app.api_route("/public/{path:path}", methods=ALL_METHODS)
async def main_site(request: Request, path: str):
    return await forward(request, MAIN_ORIGIN + request.url.path)


@app.api_route("/msg/{path:path}", methods=ALL_METHODS)
async def chat_service(request: Request, path: str):
    return await forward(request, CHAT_ORIGIN + request.url.path)


@app.api_route("/proxy", methods=ALL_METHODS)
async def generic_proxy(request: Request, url: Optional[str] = None):
    """通用代理(白名单限制),用于 picapp/bbs 图片等零散资源。"""
    if not url:
        return JSONResponse({"proxy_error": "缺少 url 参数"}, status_code=400)
    from urllib.parse import urlparse as _up
    u = _up(url)
    if u.scheme not in ("http", "https") or u.netloc not in ALLOWED_PROXY_HOSTS:
        # 私信图片等新图床域名可能不在白名单——拒绝必须留痕,排障时从日志直接
        # 读到被拒 URL 即可定位要加白的域名(而不是对着裂图猜)
        logger.warning("[proxy] 拒绝非白名单目标: %s", url)
        return JSONResponse({"proxy_error": f"目标不在白名单: {u.netloc}"}, status_code=403)
    return await forward(request, url, keep_query=False, follow_redirects=True)


@app.get("/link")
async def link_through(request: Request, url: Optional[str] = None):
    """帖内外链的"跳桥":不带 Cookie 服务端抓取,绕开证书不匹配的短链域名。

    背景:帖子里常见的淘宝短链 http(s)://e.tb.cn/** 会 301 强制 https,
    而该域名 443 返回的证书是 *.alibabacorp.com(不匹配),浏览器直接报
    "您的连接不是私密连接"。这里用 verify=False 服务端抓取:
      - 同主机的重定向(如 http→https 强制升级)由服务端继续跟,
        避免浏览器撞上坏证书
      - 跨主机重定向 → 把 Location 透传给浏览器(目标通常证书正常)
      - 2xx HTML → 同源返回页面,让页面自身的 JS/meta 跳转到真实地址
    不携带任何 sdgun 会话 Cookie,避免泄露;仅 GET。
    """
    if not url:
        return JSONResponse({"proxy_error": "缺少 url 参数"}, status_code=400)
    from urllib.parse import urlparse as _up
    u = _up(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return JSONResponse({"proxy_error": "仅支持 http/https 链接"}, status_code=400)

    def fetch():
        import warnings
        from urllib.parse import urljoin
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")  # 屏蔽 verify=False 的 InsecureRequestWarning
            current = url
            for _ in range(5):  # 最多跟 5 跳
                resp = upstream_client.get(current, timeout=UPSTREAM_TIMEOUT, allow_redirects=False,
                                           verify=False,
                                           headers={"User-Agent": APP_HEADERS["User-Agent"]})
                if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                    nxt = urljoin(current, resp.headers["location"])
                    if _up(nxt).netloc.lower() == _up(current).netloc.lower():
                        current = nxt   # 同主机:继续服务端跟(http→https 升级等)
                        continue
                    resp.headers["x-next-location"] = nxt  # 跨主机:交给浏览器
                return resp
            return resp
            # unreachable

    try:
        resp = await run_in_threadpool(fetch)
    except upstream_lib.RequestException as exc:
        return JSONResponse({"proxy_error": f"{type(exc).__name__}: {exc}"}, status_code=502)

    if 300 <= resp.status_code < 400 and resp.headers.get("x-next-location"):
        return Response(status_code=302, headers={"Location": resp.headers["x-next-location"]})

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in STRIP_RESP_HEADERS and k.lower() != "x-next-location"
    }
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)


# ============================ 前端静态托管(单程序模式,必须放最后) ============================

# 存在 webui/dist(npm run build 产物)时,由本服务直接托管页面,
# 一个程序同时提供页面与代理(同源,无 CORS)。开发时仍可用 vite:5173 + .env.development。
# 注意:根路径挂载会吞掉之后注册的路由,因此必须放在所有 @app 路由之后。

from fastapi.staticfiles import StaticFiles  # noqa: E402

WEBUI_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "dist")
if os.path.isdir(WEBUI_DIST):
    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
    logger.info("[webui] 已托管前端构建产物: %s → http://127.0.0.1:8000/", WEBUI_DIST)
else:
    logger.info("[webui] 未发现 webui/dist(仅代理模式;在 webui/ 下 npm run build 可启用单程序模式)")
