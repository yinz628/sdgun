# -*- coding: utf-8 -*-
"""帖子收藏(网页端本地)与 /favorites 路由。

与点赞归档(sdgun_likes)同一模式。区别:收藏可取消(remove),因此是完整的
本地标记功能而非单向历史。上游的收藏接口未逆向出(个人中心有「收藏」入口,
但 9 份抓包均无调用记录;collect.magcloud.net 实为官方数据收集域,非用户收藏),
当前为纯本地实现——上游接口逆向后可升级为云端同步(届时本文件仍是缓存层)。

归档文件 sdgun_favorites.json 在 DATA_DIR(VPS 挂载卷),重建容器不丢;
以 tid 去重;上限 MAX_FAVORITES 条,最旧先出。
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

logger = logging.getLogger("sdgun.favorites")

MAX_FAVORITES = 1000


class FavoriteStore:
    """线程安全的收藏存储(tid 去重,可增删,added_at 倒序,原子落盘)。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "sdgun_favorites.json")
        # RLock(可重入):add/remove 持锁调 _persist → list() 再进锁
        self._lock = threading.RLock()
        self._items: dict[int, dict] = {}   # tid -> {"tid","title","added_at"}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                items = json.load(f).get("favorites") or []
            self._items = {i["tid"]: i for i in items if isinstance(i.get("tid"), int)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if os.path.exists(self.path):
                logger.warning("收藏读取失败(%s),从空库开始: %s", self.path, exc)

    def _persist(self) -> None:
        try:
            atomic_write_json(self.path, {"favorites": self.list()})
        except OSError as exc:
            logger.warning("收藏落盘失败(%s): %s", self.path, exc)

    def add(self, tid: int, title: str) -> bool:
        """收藏(幂等,重复收藏刷新时间与标题),返回是否新增加。"""
        added = False
        with self._lock:
            if tid not in self._items:
                added = True
            self._items[tid] = {"tid": tid, "title": title or "(无标题)",
                                "added_at": int(time.time())}
            while len(self._items) > MAX_FAVORITES:
                oldest = min(self._items, key=lambda k: self._items[k]["added_at"])
                self._items.pop(oldest)
            self._persist()
        return added

    def remove(self, tid: int) -> bool:
        """取消收藏,返回是否确实删除了。"""
        with self._lock:
            removed = self._items.pop(tid, None) is not None
            if removed:
                self._persist()
        return removed

    def list(self, limit: int = 500) -> list[dict]:
        with self._lock:
            items = sorted(self._items.values(), key=lambda i: i["added_at"], reverse=True)
        return items[:max(1, limit)]

    def count(self) -> int:
        with self._lock:
            return len(self._items)


store = FavoriteStore(os.environ.get("SDGUN_DATA_DIR") or
                      os.path.dirname(os.path.abspath(__file__)))

router = APIRouter()


class FavoriteBody(BaseModel):
    tid: int
    title: str = ""


@router.post("/favorites/add")
async def favorites_add(body: FavoriteBody):
    return {"added": store.add(body.tid, body.title), "total": store.count()}


@router.post("/favorites/remove")
async def favorites_remove(body: FavoriteBody):
    return {"removed": store.remove(body.tid), "total": store.count()}


@router.get("/favorites/list")
async def favorites_list(limit: Optional[int] = 500):
    return {"favorites": store.list(limit=limit), "total": store.count()}


@router.get("/favorites/has")
async def favorites_has(tid: int):
    """单条状态查询(帖子详情打开时用,避免拉全量列表)。"""
    with store._lock:
        return {"has": tid in store._items}
