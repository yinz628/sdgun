# -*- coding: utf-8 -*-
"""
SDGun「水弹社区」API 最简测试脚本（Python + requests）

通道结论（来自抓包分析）：
  - WAP 通道：只靠 Cookie + X-Requested-With + Referer，无需 mag-ss-key
  - 原生通道：额外要求 mag-ss-key（RSA 加密，每个请求都变），网页版无法直接生成，
    脚本里会试探性调用一次，用来验证服务端是否强制校验
会话建立：
  - 从抓包文件自动提取 Cookie；也可手动填 CONFIG.COOKIES
Token 机制：
  - 主站 token：GET /mag/index/v1/Index/jsApi → _USER.token（与主 Cookie 值一致）
  - 聊天 token：POST /mag/user/v1/User/repairChatAndResToken → data.chatToken
运行：python sdgun_api_test.py
"""

import json
import os
import sys
from urllib.parse import urlparse, parse_qsl

import requests

# ============================ 配置 ============================

BASE = "https://mag1.sdgun.net"

# 抓包文件路径（用于自动提取 Cookie）；不想自动提取就设为 None 并手动填 COOKIES
CAPTURE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "sdgun-2026-08-24.json")

# 手动填 Cookie 时用这个（从抓包请求头里复制三个值）：
#   3df5d0fc98d8c119af2e389a3f45b5b0=<会话token>; PHPSESSID=...; server_session_434c27a8=...
COOKIES: dict[str, str] = {}

# 测试用的帖子（来自抓包样本）
TID = 4231648        # 有评论的帖子
FID = 153            # 所属版块
CHANNEL_ID = 6       # 首页"聚焦"频道
CAT_ID = 101         # 信息流分类：公告

TIMEOUT = 15

# ============================ 会话与 Token ============================

def load_cookies_from_capture(path: str) -> dict[str, str]:
    """从 Anything Analyzer 导出的抓包 JSON 里提取 mag1.sdgun.net 的 Cookie。"""
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    for r in records:
        if "mag1.sdgun.net" not in (r.get("url") or ""):
            continue
        raw = r.get("request_headers")
        if not raw:
            continue
        try:
            headers = json.loads(raw)          # 抓包里 headers 是 JSON 字符串
        except ValueError:
            continue
        cookie_str = headers.get("cookie") or headers.get("Cookie")
        if cookie_str:
            return dict(pair.split("=", 1) for pair in cookie_str.split("; ") if "=" in pair)
    return {}


def build_session() -> requests.Session:
    """建立带会话 Cookie 的 requests.Session（相当于浏览器登录态）。"""
    s = requests.Session()

    cookies = COOKIES or load_cookies_from_capture(CAPTURE_FILE) if CAPTURE_FILE else COOKIES
    if not cookies:
        print("[FATAL] 没有可用的 Cookie：请把抓包 JSON 放在同目录，或手动填 COOKIES")
        sys.exit(1)
    s.cookies.update(cookies)
    print(f"[OK] 已加载 {len(cookies)} 个 Cookie: {', '.join(cookies.keys())}")

    # WAP 通道公共头（分析结论：mag-ss-key 不需要）
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_1_1 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/",                # 防盗链/来源校验
        "Accept": "*/*",
    })
    return s


def fetch_main_token(s: requests.Session) -> dict:
    """GET /mag/index/v1/Index/jsApi → 当前用户信息 + 主站 token。"""
    data = get_json(s, "/mag/index/v1/Index/jsApi")
    user = (data or {}).get("_USER") or {}
    if not user:
        raise RuntimeError("jsApi 未返回 _USER，Cookie 可能已过期")
    # 敏感字段脱敏（返回值也用脱敏版，避免 run_case 二次打印时泄露）
    masked = dict(user)
    if masked.get("phone"):
        masked["phone"] = masked["phone"][:3] + "****" + masked["phone"][-2:]
    if masked.get("token"):
        masked["token"] = masked["token"][:6] + "..."
    print(f"[OK] 当前用户: user_id={user.get('user_id')} name={user.get('name')} "
          f"用户组={user.get('group_name')}")
    return masked


def fetch_chat_token(s: requests.Session, user_id) -> str:
    """POST /mag/user/v1/User/repairChatAndResToken → 聊天服务 token。"""
    data = post_form(s, "/mag/user/v1/User/repairChatAndResToken",
                     {"user_id": str(user_id)})
    chat_token = ((data or {}).get("data") or {}).get("chatToken")
    if not chat_token:
        raise RuntimeError("未取到 chatToken")
    print(f"[OK] chatToken: {chat_token[:6]}...")
    return chat_token


def chat_unread(s: requests.Session, chat_token: str, user_id) -> dict:
    """聊天服务未读数（独立域名，query 鉴权，注意它是 http 明文）。"""
    resp = s.get(
        "http://shuidan.chat.magcloud.net/msg/unReadMsg",
        params={"app_key": "shuidan", "step": 100,
                "token": chat_token, "user_id": f"user{user_id}"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()

# ============================ 请求封装（含错误处理） ============================

def _check(resp: requests.Response, path: str):
    if resp.status_code != 200:
        raise requests.HTTPError(f"HTTP {resp.status_code} @ {path}")
    ct = resp.headers.get("Content-Type", "")
    if "json" not in ct:
        raise ValueError(f"非 JSON 响应（{ct}），Cookie 可能过期或被重定向到登录页 @ {path}")


def _unwrap(payload: dict) -> dict | None:
    """统一包裹 {success, code, msg, ...}：code=100 成功。"""
    if payload.get("success") is not True:
        raise ValueError(f"success=false, code={payload.get('code')}, msg={payload.get('msg')}")
    return payload


def get_json(s: requests.Session, path: str, params: dict | None = None) -> dict:
    resp = s.get(BASE + path, params=params, timeout=TIMEOUT)
    _check(resp, path)
    return resp.json()


def post_form(s: requests.Session, path: str, form: dict) -> dict:
    resp = s.post(BASE + path, data=form, timeout=TIMEOUT)
    _check(resp, path)
    return resp.json()


def run_case(title: str, fn):
    """统一执行 + 错误处理 + 结果打印。"""
    print(f"\n{'=' * 70}\n[CASE] {title}")
    try:
        result = fn()
        text = json.dumps(result, ensure_ascii=False)
        print(f"[PASS] {text[:400]}{'...(截断)' if len(text) > 400 else ''}")
        return result
    except (requests.RequestException, ValueError, RuntimeError) as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return None

# ============================ 主流程 ============================

def main():
    s = build_session()

    # ---- 1. 全局配置（无强鉴权，验证连通性）----
    run_case("全局配置 GET /mag/index/v2/index/config",
             lambda: _unwrap(get_json(s, "/mag/index/v2/index/config")))

    # ---- 2. 当前用户 + 主站 token ----
    user = run_case("当前用户 GET /mag/index/v1/Index/jsApi（提取主站 token）",
                    lambda: fetch_main_token(s))
    if not user:
        print("\n[STOP] 拿不到用户信息（Cookie 失效），后面的用例跳过。"
              "请重新抓包更新 Cookie 后再试。")
        return
    user_id = user["user_id"]

    # ---- 3. 聊天 token + 未读数（Token 链路验证）----
    chat_token = run_case("换聊天 token POST /mag/user/v1/User/repairChatAndResToken",
                          lambda: fetch_chat_token(s, user_id))
    if chat_token:
        run_case("聊天未读 GET shuidan.chat.magcloud.net/msg/unReadMsg",
                 lambda: chat_unread(s, chat_token, user_id))

    # ---- 4. 帖子详情（HTML，验证 WAP 页面可用）----
    def thread_html():
        resp = s.get(BASE + "/mag/circle/v1/forum/threadViewPage",
                     params={"tid": TID, "mag_hide_progress": 1, "themecolor": "111111"},
                     timeout=TIMEOUT)
        resp.raise_for_status()
        html = resp.text
        if "tid" not in html or len(html) < 1000:
            raise ValueError("HTML 内容异常")
        title = html.split("<title>")[1].split("</title>")[0] if "<title>" in html else "?"
        return {"标题": title, "长度": len(html), "前120字符": html[:120]}
    run_case(f"帖子详情(HTML) GET /mag/circle/v1/forum/threadViewPage?tid={TID}", thread_html)

    # ---- 5. 评论列表 ----
    run_case("评论列表 GET /mag/circle/v2/Forum/commentList",
             lambda: _unwrap(get_json(s, "/mag/circle/v2/Forum/commentList",
                                      {"tid": TID, "fid": FID, "order": 2,
                                       "p": 1, "step": 10, "authorid": 0})))

    # ---- 6. 话题内容（POST form）----
    run_case("帖子话题 POST /mag/circle/v4/show/loadContentSubject",
             lambda: _unwrap(post_form(s, "/mag/circle/v4/show/loadContentSubject",
                                       {"content_id": str(TID), "type": "forum"})))

    # ---- 7. 悬赏红包 ----
    run_case("悬赏红包 POST /mag/postreward/v1/ReleaseReward/getRewardContent",
             lambda: _unwrap(post_form(s, "/mag/postreward/v1/ReleaseReward/getRewardContent",
                                       {"type_value": str(TID), "type": 1})))

    # ---- 8. 原生通道试探（预期可能失败：无 mag-ss-key）----
    run_case("[试探] 原生接口 GET /mag/info/v2/channel/infoListByCatId（不带 mag-ss-key）",
             lambda: _unwrap(get_json(s, "/mag/info/v2/channel/infoListByCatId",
                                      {"channel_id": CHANNEL_ID, "cat_id": 2,
                                       "p": 1, "step": 5, "tab": 0})))

    print("\n" + "=" * 70)
    print("测试结束。若用例 8 失败而 1-7 成功，说明 info 类原生接口强制校验"
          " mag-ss-key，网页版需改走 WAP 接口或让后端放行。")


if __name__ == "__main__":
    main()
