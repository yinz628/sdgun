# -*- coding: utf-8 -*-
"""点赞本地归档(网页端点赞历史)与 /likes 路由。

背景:上游没有"我点赞过的帖子"查询接口(App 个人中心无此入口,9 份抓包均
未见),App 只记在本地。本模块让网页端的每次点赞落盘归档,前端在个人主页
「点赞」tab 回看——与 sdgun_messages(私信归档)同一模式:
  - 归档文件 sdgun_likes.json 在 DATA_DIR(VPS 挂载卷),重建容器不丢
  - 以 tid 去重(重复点赞刷新时间);上限 MAX_LIKES 条,最旧先出
  - 只能从功能上线起累积,此前(App/旧网页)的点赞上游无法找回(上游限制)

归档入口:前端点赞成功(ThreadPanel doApplaud,code=100/101 均算)后
POST /likes/add {tid, title}——标题由前端带上,代理不解析帖子内容。
"""

import json
import logging
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from sdgun_session import atomic_write_json

logger = logging.getLogger("sdgun.likes")

MAX_LIKES = 2000


class LikeStore:
    """线程安全的点赞归档(按 tid 去重,liked_at 倒序,原子落盘)。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "sdgun_likes.json")
        # RLock(可重入):add 持锁调 _persist → list() 再进锁;普通 Lock 会死锁
        self._lock = threading.RLock()
        self._likes: dict[int, dict] = {}   # tid -> {"tid", "title", "liked_at"}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                likes = json.load(f).get("likes") or []
            self._likes = {l["tid"]: l for l in likes if isinstance(l.get("tid"), int)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if os.path.exists(self.path):
                logger.warning("点赞归档读取失败(%s),从空库开始: %s", self.path, exc)

    def _persist(self) -> None:
        try:
            atomic_write_json(self.path, {"likes": self.list()})
        except OSError as exc:
            logger.warning("点赞归档落盘失败(%s): %s", self.path, exc)

    def add(self, tid: int, title: str) -> bool:
        """归档/刷新一条点赞(幂等,重复点赞更新时间),返回是否新增加。"""
        added = False
        with self._lock:
            if tid not in self._likes:
                added = True
            self._likes[tid] = {"tid": tid, "title": title or "(无标题)",
                                "liked_at": int(time.time())}
            while len(self._likes) > MAX_LIKES:
                oldest = min(self._likes, key=lambda k: self._likes[k]["liked_at"])
                self._likes.pop(oldest)
            self._persist()
        return added

    def list(self, limit: int = 200) -> list[dict]:
        """按点赞时间倒序返回(默认最近 200 条)。"""
        with self._lock:
            items = sorted(self._likes.values(), key=lambda l: l["liked_at"], reverse=True)
        return items[:max(1, limit)]

    def count(self) -> int:
        with self._lock:
            return len(self._likes)


store = LikeStore(os.environ.get("SDGUN_DATA_DIR") or
                  os.path.dirname(os.path.abspath(__file__)))

router = APIRouter()


class LikeAddBody(BaseModel):
    tid: int
    title: str = ""


@router.post("/likes/add")
async def likes_add(body: LikeAddBody):
    """前端点赞成功后上报归档(幂等;归档失败不影响点赞本身——这里只做本地写)。"""
    added = store.add(body.tid, body.title)
    return {"added": added, "total": store.count()}


@router.get("/likes/list")
async def likes_list(limit: Optional[int] = 200):
    """点赞历史(liked_at 倒序),供个人主页「点赞」tab 使用。"""
    return {"likes": store.list(limit=limit), "total": store.count()}
