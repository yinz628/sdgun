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
import threading
from typing import Optional

from fastapi import APIRouter

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
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"msgs": self._sorted()}, f, ensure_ascii=False)
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
