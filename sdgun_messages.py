# -*- coding: utf-8 -*-
"""私信本地归档(代理侧消息库)与 /messages/local 查询路由。

背景(附录 5/8):上游私信无历史接口,unReadMsg 拉取即清(App 与网页共用消费池)——
消息一旦被任何一方读走就再也无法从服务端取回。本模块把每条经过代理的消息
(收到的 + 发出的)落盘归档,让"历史记录"从部署起本地累积:
  - 归档文件 sdgun_messages.json 在 DATA_DIR(VPS 挂载卷),重建容器不丢
  - 以 msg_id 去重;上限 MAX_MESSAGES 条 FIFO 裁剪,防止无限膨胀
  - App 先读走的消息代理看不到,归档因此不完整——这是上游限制,已在文档声明

归档钩子:sdgun_proxy.forward 对 /msg/unReadMsg(响应 msgs[])与
/msg/sendMsg(响应 data 单条)调用 absorb_upstream,离线纯逻辑可单测。
"""

import json
import logging
import os
import re
import threading
from typing import Optional

from fastapi import APIRouter

from sdgun_session import atomic_write_json

logger = logging.getLogger("sdgun.messages")

MAX_MESSAGES = 5000


class MessageStore:
    """线程安全的本地消息归档(按 msg_id 去重,FIFO 裁剪,落盘持久化)。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "sdgun_messages.json")
        self._lock = threading.Lock()
        self._msgs: dict[int, dict] = {}   # msg_id -> 消息体
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                msgs = json.load(f).get("msgs") or []
            self._msgs = {m["msg_id"]: m for m in msgs if isinstance(m.get("msg_id"), int)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if os.path.exists(self.path):
                logger.warning("消息归档读取失败(%s),从空库开始: %s", self.path, exc)

    def _persist(self) -> None:
        # 原子写(同 sdgun_session.SessionStore):并发归档时避免交错写坏 JSON
        try:
            atomic_write_json(self.path, {"msgs": self._sorted()})
        except OSError as exc:
            logger.warning("消息归档落盘失败(%s): %s", self.path, exc)

    def _sorted(self) -> list[dict]:
        return sorted(self._msgs.values(), key=lambda m: m.get("create_time") or 0)

    # ---- 写入 ----

    def absorb(self, msgs: list[dict]) -> int:
        """归档一批消息,返回新增条数(按 msg_id 去重)。"""
        added = 0
        with self._lock:
            for m in msgs:
                mid = m.get("msg_id")
                if not isinstance(mid, int) or mid in self._msgs:
                    continue
                self._msgs[mid] = m
                added += 1
            while len(self._msgs) > MAX_MESSAGES:   # FIFO 裁剪(按 create_time 最旧)
                oldest = min(self._msgs, key=lambda k: self._msgs[k].get("create_time") or 0)
                self._msgs.pop(oldest)
            if added:
                self._persist()
        return added

    # ---- 读取 ----

    def count(self) -> int:
        with self._lock:
            return len(self._msgs)

    def list(self, peer_id: Optional[str] = None, limit: int = 1000) -> list[dict]:
        """按时间升序返回归档消息;peer_id 给定时只返回与该对端的双向消息。"""
        with self._lock:
            msgs = self._sorted()
        if peer_id:
            msgs = [m for m in msgs
                    if m.get("from_user_id") == peer_id or m.get("to_user_id") == peer_id]
        return msgs[-max(1, limit):]

    def conversations(self, me: str) -> "list[dict]":
        # 注解加引号:类体内 list 已被同名方法遮蔽,裸 list[dict] 会在类创建时炸
        """按对端分组的会话摘要(me=user<uid>),按最后消息时间倒序。

        供网页端「私信」会话列表使用:每对端返回最后一条消息(摘录/时间/是否我发的)、
        总条数、对端昵称(取自该对端来信 ext.user_name,可能为空——前端可再查 loadUsers)。
        """
        with self._lock:
            msgs = self._sorted()
        out: dict[str, dict] = {}
        for m in msgs:
            frm, to = m.get("from_user_id") or "", m.get("to_user_id") or ""
            peer = to if frm == me else frm
            if not peer or peer == me:
                continue
            cur = out.setdefault(peer, {"peer_id": peer, "name": "", "last_text": "",
                                        "last_time": 0, "last_from_me": False, "count": 0})
            cur["count"] += 1
            text, sender_name = _excerpt(m)
            if not cur["name"] and sender_name and frm == peer:
                cur["name"] = sender_name
            t = m.get("create_time") or 0
            if t >= cur["last_time"]:
                cur["last_time"] = t
                cur["last_text"] = text[:80]
                cur["last_from_me"] = frm == me
        return sorted(out.values(), key=lambda c: c["last_time"], reverse=True)


# 图片消息的字段名上游未文档化(手头抓包无样本),按"长得像图片 URL"自适应识别;
# 与前端 webui/src/utils/chat.ts 的 parseChatMessage 是同一策略的两份实现,改识别
# 规则时两处同步
_IMG_URL_RE = re.compile(
    r"^(?:https?://(?:\S+\.(?:jpe?g|png|gif|webp|bmp)(?:[?#]\S*)?"
    r"|(?:picapp\.sdgun\.net|pic\.magcloud\.cc|bbs\.sdgun\.com\.cn"
    r"|sdgun\.ymgames\.com\.cn|shuidan\.app1\.magcloud\.net)/\S*"
    r"|[^/]+/(?:public/)?download_pic/\S*)"
    r"|/\S+\.(?:jpe?g|png|gif|webp|bmp)(?:[?#]\S*)?)$",   # 聊天服务相对路径
    re.IGNORECASE,
)


def _find_image_urls(obj) -> list[str]:
    """递归收集 JSON 值里的图片 URL(任意字段名)。"""
    out: list[str] = []
    if isinstance(obj, str):
        if _IMG_URL_RE.match(obj):
            out.append(obj)
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find_image_urls(v))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_find_image_urls(v))
    return out


def _excerpt(m: dict) -> tuple[str, str]:
    """(正文摘录, 发送者昵称)——content 常为 {"text": "..."} JSON,ext 常带 user_name。

    图片消息(无 text 字段)摘录为「[图片]」,避免会话列表里露出原始 JSON。
    """
    text = str(m.get("content") or "")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if parsed.get("text"):
                text = str(parsed["text"])
            elif _find_image_urls(parsed):
                text = "[图片]"
    except ValueError:
        pass
    name = ""
    try:
        ext = json.loads(m.get("ext") or "{}")
        if isinstance(ext, dict):
            name = str(ext.get("user_name") or "")
    except ValueError:
        pass
    return text, name


def absorb_upstream(path: str, content: bytes, store: MessageStore) -> None:
    """从代理转发的 /msg 响应体中提取消息并归档(解析失败静默,不影响转发)。"""
    try:
        payload = json.loads(content)
    except ValueError:
        return
    if path.endswith("/msg/unReadMsg"):
        msgs = payload.get("msgs") or []
    elif path.endswith("/msg/sendMsg"):
        data = payload.get("data")
        msgs = [data] if isinstance(data, dict) else []
    else:
        return
    if msgs:
        added = store.absorb(msgs)
        if added:
            logger.info("[messages] 归档 %d 条新私信(总计 %d)", added, store.count())


store = MessageStore(os.environ.get("SDGUN_DATA_DIR") or
                     os.path.dirname(os.path.abspath(__file__)))

router = APIRouter()


@router.get("/messages/local")
async def local_messages(peer_id: Optional[str] = None, limit: int = 1000):
    """本地归档的私信历史(peer_id=user<uid> 时只返回与该对端的双向消息)。

    归档自部署本功能起累积;此前已被消费的消息上游无法找回。
    """
    return {"msgs": store.list(peer_id=peer_id, limit=limit), "total": store.count()}


@router.get("/messages/conversations")
async def local_conversations(me: str):
    """本地归档的会话列表(me=user<uid>,按对端分组摘要,最后消息时间倒序)。"""
    return {"conversations": store.conversations(me)}
