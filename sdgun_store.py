# -*- coding: utf-8 -*-
"""JSON 归档基类(点赞/收藏等本地标记存储的公共样板)。

背景:sdgun_likes.LikeStore 与 sdgun_favorites.FavoriteStore 曾各写一份同构样板
(模块级单例、基于 SDGUN_DATA_DIR 的文件路径、坏文件 fail-open 载入、atomic_write_json
原子落盘、RLock、上限最旧先出裁剪)。这里把公共部分收敛成 BaseJsonStore,子类只声明
文件/载荷/字段配置与上限来源。

设计要点:
  - _load 坏文件一律 fail-open(从空库开始并告警):坏文件的症状是"标记静默丢失",
    宁可丢历史也不要让 import 期抛异常把整个代理打挂。
  - 锁用 RLock(可重入):add 持锁调 _persist → list() 再进锁,普通 Lock 会死锁。
  - _persist 与既有实现一致:以 self.list()(子类默认上限)为载荷,经
    sdgun_session.atomic_write_json 落盘。
  - 上限由子类 _max_items() 在调用时解析模块级常量,便于测试 monkeypatch 调小。

messages/watch 未迁移到此基类:前者用普通 Lock + 按 create_time 排序的批量 absorb,
后者是多字段状态机(rules/hits/seen/source/poll/settings),锁与裁剪语义都与本基类
不同,强行收敛会改变行为(宁缺勿破)。
"""

import json
import logging
import os
import threading
import time

from sdgun_session import atomic_write_json

logger = logging.getLogger("sdgun.store")


class BaseJsonStore:
    """线程安全的 JSON 归档基类(RLock + 坏文件 fail-open + 原子落盘 + 上限裁剪)。

    子类需提供:
      FILENAME      归档文件名(拼在 data_dir 下)
      PAYLOAD_KEY   载荷顶层键(如 "likes"/"favorites")
      TIME_FIELD    裁剪/排序用的时间字段(如 "liked_at"/"added_at")
      LOG_LABEL     告警前缀(如 "点赞归档"/"收藏"),保持原有日志文案
      logger        子类自己的 logger(保持日志通道不变)
      _max_items()  返回当前上限(读模块级常量,支持测试 monkeypatch)
    """

    FILENAME = ""
    PAYLOAD_KEY = ""
    ID_FIELD = "tid"
    TIME_FIELD = ""
    LOG_LABEL = "归档"
    logger = logger

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, self.FILENAME)
        # RLock(可重入):add 持锁调 _persist → list() 再进锁;普通 Lock 会死锁
        self._lock = threading.RLock()
        self._items: dict[int, dict] = {}
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                items = json.load(f).get(self.PAYLOAD_KEY) or []
            self._items = {i[self.ID_FIELD]: i for i in items
                           if isinstance(i.get(self.ID_FIELD), int)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if os.path.exists(self.path):
                self.logger.warning("%s读取失败(%s),从空库开始: %s",
                                    self.LOG_LABEL, self.path, exc)

    def _persist(self) -> None:
        try:
            atomic_write_json(self.path, {self.PAYLOAD_KEY: self.list()})
        except OSError as exc:
            self.logger.warning("%s落盘失败(%s): %s", self.LOG_LABEL, self.path, exc)

    def _max_items(self) -> int:
        raise NotImplementedError

    def _trim_locked(self) -> None:
        """上限裁剪:最旧先出(调用方已持 RLock)。"""
        while len(self._items) > self._max_items():
            oldest = min(self._items, key=lambda k: self._items[k][self.TIME_FIELD])
            self._items.pop(oldest)

    # ---- 写入 ----

    def add(self, tid: int, title: str) -> bool:
        """归档/刷新一条(幂等,重复添加刷新时间),返回是否新增加。"""
        added = False
        with self._lock:
            if tid not in self._items:
                added = True
            self._items[tid] = {"tid": tid, "title": title or "(无标题)",
                                self.TIME_FIELD: int(time.time())}
            self._trim_locked()
            self._persist()
        return added

    # ---- 读取 ----

    def list(self, limit: int) -> list[dict]:
        """按时间倒序返回(条数由子类默认参数给出)。"""
        with self._lock:
            items = sorted(self._items.values(),
                           key=lambda i: i[self.TIME_FIELD], reverse=True)
        return items[:max(1, limit)]

    def count(self) -> int:
        with self._lock:
            return len(self._items)
