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

import logging
import os
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from sdgun_store import BaseJsonStore

logger = logging.getLogger("sdgun.likes")

MAX_LIKES = 2000


class LikeStore(BaseJsonStore):
    """线程安全的点赞归档(按 tid 去重,liked_at 倒序,原子落盘)。

    公共样板(路径/RLock/坏文件 fail-open 载入/原子落盘/上限裁剪)见 sdgun_store。
    """

    FILENAME = "sdgun_likes.json"
    PAYLOAD_KEY = "likes"
    TIME_FIELD = "liked_at"
    LOG_LABEL = "点赞归档"
    logger = logger

    def _max_items(self) -> int:
        return MAX_LIKES

    @property
    def _likes(self) -> dict:
        """兼容既有属性名(内部/测试按 _likes 访问,实际存于基类 _items)。"""
        return self._items

    def list(self, limit: int = 200) -> list[dict]:
        """按点赞时间倒序返回(默认最近 200 条)。"""
        return super().list(limit)


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
