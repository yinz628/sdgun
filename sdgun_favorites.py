# -*- coding: utf-8 -*-
"""帖子收藏(网页端本地)与 /favorites 路由。

与点赞归档(sdgun_likes)同一模式。区别:收藏可取消(remove),因此是完整的
本地标记功能而非单向历史。上游的收藏接口未逆向出(个人中心有「收藏」入口,
但 9 份抓包均无调用记录;collect.magcloud.net 实为官方数据收集域,非用户收藏),
当前为纯本地实现——上游接口逆向后可升级为云端同步(届时本文件仍是缓存层)。

归档文件 sdgun_favorites.json 在 DATA_DIR(VPS 挂载卷),重建容器不丢;
以 tid 去重;上限 MAX_FAVORITES 条,最旧先出。
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from sdgun_store import BaseJsonStore

logger = logging.getLogger("sdgun.favorites")

MAX_FAVORITES = 1000


class FavoriteStore(BaseJsonStore):
    """线程安全的收藏存储(tid 去重,可增删,added_at 倒序,原子落盘)。

    公共样板(路径/RLock/坏文件 fail-open 载入/原子落盘/上限裁剪)见 sdgun_store。
    """

    FILENAME = "sdgun_favorites.json"
    PAYLOAD_KEY = "favorites"
    TIME_FIELD = "added_at"
    LOG_LABEL = "收藏"
    logger = logger

    def _max_items(self) -> int:
        return MAX_FAVORITES

    def remove(self, tid: int) -> bool:
        """取消收藏,返回是否确实删除了。"""
        with self._lock:
            removed = self._items.pop(tid, None) is not None
            if removed:
                self._persist()
        return removed

    def list(self, limit: int = 500) -> list[dict]:
        return super().list(limit)


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
