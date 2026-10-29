# -*- coding: utf-8 -*-
"""已读状态的服务器端存储(两个 id 集合)与 /uistate 路由。

背景:通知已读游标(sdgunNotifySeen:code → 最后看到的 create_time)与关注命中
「已读帖子」集合(sdgunWatchReadTids)此前只存浏览器 localStorage——换设备/换浏览器
已读状态就丢,同一账号在多端看到的角标互不相通。本模块把这两个集合搬到服务端
(VPS 数据卷),localStorage 降级为写穿缓存;服务端不可用时前端静默回落纯本地,
不影响页面。

数据文件 sdgun_uistate.json 在 DATA_DIR(VPS 挂载卷),重建容器不丢:
  {"notify_seen": [...], "watch_read_tids": [...]}

两个集合语义不同,裁剪方式也不同:
  - watch_read_tids:数值集合,tid 单调递增 → 保留**最新** MAX_WATCH_READ 个(数值降序)。
  - notify_seen:不透明令牌集合(前端把 code→create_time 编码成 "code:ts" 字符串),
    服务端不解析其含义,按**追加序**保留,超出上限从头部(最早出现)丢弃。

写入语义:POST /uistate/seen 只做**并集**合并(永不删除)——已读是单调的,跨设备
并集即正确的合并。因此本模块只提供 merge,不提供 remove。
"""

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from sdgun_session import atomic_write_json
from sdgun_store import BaseJsonStore

logger = logging.getLogger("sdgun.uistate")

MAX_NOTIFY_SEEN = 500
MAX_WATCH_READ = 2000


def _norm_notify_ids(raw) -> list[str]:
    """归一化通知令牌列表:转字符串、去空、保序去重。"""
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        token = str(item)
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _norm_watch_tids(raw) -> list[int]:
    """归一化 tid 列表:只留正整数,去重。"""
    out: set[int] = set()
    if not isinstance(raw, (list, tuple)):
        return []
    for item in raw:
        try:
            tid = int(item)
        except (TypeError, ValueError):
            continue
        if tid > 0:
            out.add(tid)
    return sorted(out, reverse=True)   # 数值降序:最新在前


class UiStateStore(BaseJsonStore):
    """已读状态存储(两个 id 集合,各自上限,原子落盘)。

    与 likes/favorites 不同:这里不是 tid → 记录的映射,而是两个独立的有序 id 集合,
    基类的「单载荷 + dict[int, dict]」模型不适用,因此覆写 _load/_persist,只复用
    其路径解析、RLock 与 atomic_write_json 落盘样板。坏文件同样 fail-open(从空库开始)。
    """

    FILENAME = "sdgun_uistate.json"
    LOG_LABEL = "已读状态"
    logger = logger

    def __init__(self, data_dir: str):
        # 先建两个集合,_load 在两个分支都会重新赋值(先给初值只为异常路径也安全)
        self._notify_seen: list[str] = []
        self._watch_read_tids: list[int] = []
        super().__init__(data_dir)

    # ---- 持久化(覆写基类:双载荷) ----

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._notify_seen = _norm_notify_ids(data.get("notify_seen"))
            self._watch_read_tids = _norm_watch_tids(data.get("watch_read_tids"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if os.path.exists(self.path):
                self.logger.warning("%s读取失败(%s),从空库开始: %s",
                                    self.LOG_LABEL, self.path, exc)
            self._notify_seen = []
            self._watch_read_tids = []

    def _persist(self) -> None:
        try:
            atomic_write_json(self.path, {
                "notify_seen": self._notify_seen,
                "watch_read_tids": self._watch_read_tids,
            })
        except OSError as exc:
            self.logger.warning("%s落盘失败(%s): %s", self.LOG_LABEL, self.path, exc)

    # ---- 读取/合并 ----

    def state(self) -> dict:
        """当前完整状态(供 GET 返回 / POST 回执)。"""
        with self._lock:
            return {"notify_seen": list(self._notify_seen),
                    "watch_read_tids": list(self._watch_read_tids)}

    def merge(self, notify_ids=None, watch_tids=None) -> dict:
        """把上报的两个集合并入现有集合并落盘,返回合并后的完整状态。

        两者均可选(前端可能只标了通知已读或只标了帖子已读)。已读单调递增,
        因此只做并集,不做删除。
        """
        with self._lock:
            self._notify_seen = self._union_notify(self._notify_seen,
                                                   _norm_notify_ids(notify_ids))
            self._watch_read_tids = self._union_watch(self._watch_read_tids,
                                                      _norm_watch_tids(watch_tids))
            self._persist()
            return self.state()

    @staticmethod
    def _union_notify(existing: list[str], incoming: list[str]) -> list[str]:
        """并集,保序(追加在尾),超出上限从头部(最早出现)丢弃。"""
        out = list(existing)
        seen = set(out)
        for token in incoming:
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out[-MAX_NOTIFY_SEEN:]

    @staticmethod
    def _union_watch(existing: list[int], incoming: list[int]) -> list[int]:
        """并集后保留最新 MAX_WATCH_READ 个(tid 单调递增,大者新)。"""
        return sorted(set(existing) | set(incoming), reverse=True)[:MAX_WATCH_READ]


store = UiStateStore(os.environ.get("SDGUN_DATA_DIR") or
                     os.path.dirname(os.path.abspath(__file__)))

router = APIRouter()


class SeenBody(BaseModel):
    """已读上报体:两个集合均可选,缺省表示"本字段本次无变化"。"""
    notify_ids: Optional[list[str]] = None
    watch_tids: Optional[list[int]] = None


@router.get("/uistate")
async def uistate_get():
    """返回服务端保存的两个已读集合(前端挂载时拉取并做并集迁移)。"""
    return store.state()


@router.post("/uistate/seen")
async def uistate_seen(body: SeenBody):
    """上报已读(并集合并 + 裁剪 + 落盘),返回合并后的完整状态。"""
    return store.merge(body.notify_ids, body.watch_tids)
