# -*- coding: utf-8 -*-
"""关注关键字新帖提醒(本地关注列表 + 代理侧轮询)与 /watch 路由。

背景:用户想"盯住某些关键字的新帖"(如「出 + glock」的二手交易),上游没有这类
订阅接口。本模块在**代理侧**维护一份关注规则并周期轮询信息流,发现新出现的帖子
主题命中规则时记录到本地;前端轮询命中记录后弹桌面式通知并累计角标。

为什么把匹配放代理侧而不是前端:
  - 页面不开也要能发现(前端轮询只在浏览器标签页存活期间有效);
  - 上游请求必须带 App 身份头与 Cookie(见 sdgun_session),前端直连会被 WAF 挡;
  - 与 sdgun_sign/sdgun_messages 同一模式:代理进程内一条守护线程 + 本地 JSON 落盘,
    前端只做展示与规则管理。

监控源(为什么用 v2 频道流):
  - `/mag/index/v2/index/pages` 的 index_fragment 里第一个 column_type==7 的栏目即
    频道(实测「玩具交易区」),其 `channelConfigById` 的 info_config[0].cat_id 即
    "最新"分类——时间序,新帖出现在最前,适合做"新帖发现";
  - v2 频道流 p=1 会返回 totalList(全量有序 id),用 id ∈ totalList 过滤可以剔除
    混进来的广告位条目(广告位不参与 totalList),再叠加"标题含商家广告"的兜底;
  - **版块源(SDGUN_WATCH_FID)**:频道流是**策展视图**,会漏新帖(2026-10-22 实测:
    玩具交易区 7/11 分钟前的新帖不在 cat3 p=1 的 50 条里,而版块 threadList
    fid=176 前 50 条就包含它们)。交易类关键字建议用 fid 源:`/mag/circle/v1/Forum/
    threadList?fid=&p=1&step=50`(条目字段名完全不同,见 _normalize_thread_item);
  - 想换成别的时间序频道可用环境变量 SDGUN_WATCH_CHANNEL_ID + SDGUN_WATCH_CAT_ID
    直接指定(两者都设才生效,跳过自动解析)。

基线语义(重要):
  首次轮询只把当前信息流里的 id 记入 seen_ids 建基线,**不产生任何命中**——否则
  第一次部署就会把首页 50 条历史帖全部弹给用户。命中因此"自部署(或首次轮询)起
  累积",不回溯历史帖。换监控源时同样自动重建基线(reset_baseline,规则与命中历史
  保留),否则新源里满屏的旧帖会被当成新帖。已知限制:上游算法若把旧帖重新翻到
  列表前列,会被当成新帖误报(列表没有可信的发布时间序,只有相对时间文案)。

配置:
  SDGUN_WATCH=0            关闭守护线程(默认开启)
  SDGUN_WATCH_INTERVAL=60  轮询间隔秒(夹在 20..3600;0 也表示关闭)
  SDGUN_WATCH_FID          版块源(fid),优先级最高,交易类关键字推荐
  SDGUN_WATCH_CHANNEL_ID / SDGUN_WATCH_CAT_ID  频道分类源(两者都设才生效)
配置值非法一律回退默认并告警——守护线程不能死于配置笔误(同 sdgun_sign)。

提醒时间窗(运行时可改,持久化在 settings.window_hours,默认 24,取值 12/24/48):
  只提醒"发布时间在 N 小时内"的帖子,用来压掉"旧帖被算法翻出"的误报。发布时间取条目
  create_time(epoch 秒,threadList 实测存在且精确);条目没有该字段(如频道源)一律
  放行——fail-open,宁可多提醒也不要因为上游少给字段就整片漏帖。

按发帖时间而非回帖时间(2026-10-22 用户反馈):
  版块 threadList 是"最后活动置顶"排序(昨天发的帖因新回复会排到 2 小时前的帖上面),
  因此:① 命中记录存 publish_time,前端按它倒序展示;② 生成命中前还会用规则创建时间
  过滤——只有发布晚于规则创建时间的帖才算命中(规则"从此刻起"生效,与基线不回溯一致)。

存储:sdgun_watch.json 在 DATA_DIR(VPS 挂载卷),重建容器不丢;原子落盘
(sdgun_session.atomic_write_json);hits ≤ MAX_HITS(1000)按 hit_id FIFO 裁剪、
seen_ids ≤ MAX_SEEN_IDS(30000,须覆盖最长窗口 48h × 峰值发帖速率)保留最近;
另按 tid 对命中做兜底去重(见 _hit_tids),防止 seen 被裁剪后老帖重复提醒。

上游 HTTP 一律用 requests(sdgun_session.upstream_client):上游 WAF 校验请求头
指纹,httpx/aiohttp 的默认头序会被判"登录信息已过期"。
"""

import json
import logging
import os
import re
import threading
import time
from typing import Optional, Union

import requests as upstream_lib
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sdgun_session import (
    MAIN_ORIGIN,
    atomic_write_json,
    call_upstream,
    store as session_store,
    upstream_client,
)

logger = logging.getLogger("sdgun.watch")

# 规则/关键字/记录上限(测试可 monkeypatch 调小)
MAX_RULES = 50
MAX_KEYWORDS_PER_RULE = 8
MAX_KEYWORD_LEN = 40
MAX_HITS = 1000
# seen_ids 的不变式:必须覆盖「最长窗口(48h)× 峰值发帖速率」,否则老帖被回帖翻回列表时
# 会因为"已见记录已被 FIFO 挤掉"而重复命中(实测交易区约 100+ 帖/小时 → 48h ≈ 5000 条;
# 30000 给了 6 倍余量)。命中记录另有按 tid 的兜底去重(见 _hit_tids),双保险。
MAX_SEEN_IDS = 30000

# 提醒时间窗(小时):只提醒"发布时间在 N 小时内"的帖子,过滤被算法翻出的旧帖
WINDOW_HOURS_CHOICES = (12, 24, 48)
DEFAULT_WINDOW_HOURS = 24

# 关键字分隔:中英文逗号/顿号/任意空白
KEYWORD_SPLIT_RE = re.compile(r"[,，、\s]+")

# 条目 link 里的帖子 id(与前端 media.ts threadTid 的正则同一口径)
TID_IN_LINK_RE = re.compile(r"tid=(\d+)")


def _to_positive_int(value) -> Optional[int]:
    """宽松转正整数(信息流 id 有时是数字、type_value 是字符串),不可转返回 None。"""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        try:
            n = int(float(value))   # "123.0" 这类上游偶发形态
        except (TypeError, ValueError):
            return None
    return n if n > 0 else None


def _too_old(create_time, now: int, window_sec: int) -> bool:
    """条目发布时间是否早于时间窗之外。

    无 create_time(如频道源条目)一律放行——fail-open:宁可多提醒一次,也不要因为
    上游少给一个字段就整片漏帖。只有明确能解析出发布时间且确实过老才跳过。
    """
    pub = _to_positive_int(create_time)
    return pub is not None and pub < now - window_sec


def _rule_eligible(create_time, rule: dict) -> bool:
    """该规则是否"来得及"管到这条帖子:只有发布晚于(或等于)规则创建时间的帖才算命中。

    规则语义是"从此刻起盯新帖"——规则创建之前发布的帖子,即使后来被回帖翻进列表
    (版块 threadList 是"最后活动置顶"排序,2026-10-22 实测:昨天发的帖因新回复排在
    2 小时前的帖上面),也不该提醒。这与基线不回溯一致,消除按回帖时间触发的误报。
    无 create_time 的条目(频道源)视为通过(fail-open)。
    """
    pub = _to_positive_int(create_time)
    if pub is None:
        return True
    created = _to_positive_int(rule.get("created_at"))
    return created is None or pub >= created


def _normalize_keywords(raw: Union[str, list, tuple, None]) -> list[str]:
    """关键字归一化:str 按分隔符切分、list 每项为一个词;strip + casefold 去重、丢空词。

    保留首次出现的大小写形态(仅用于去重比较时 casefold),展示时按用户输入原样。
    """
    if isinstance(raw, str):
        parts: list = KEYWORD_SPLIT_RE.split(raw)
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return []
    words: list[str] = []
    seen: set[str] = set()
    for p in parts:
        w = str(p).strip()
        if not w:
            continue
        key = w.casefold()
        if key in seen:
            continue
        seen.add(key)
        words.append(w)
    return words


class WatchStore:
    """线程安全的关注规则 + 命中记录存储(原子落盘,命中/已见 id FIFO 裁剪)。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "sdgun_watch.json")
        # RLock(可重入):ingest 持锁调 match/裁剪 → 再进锁;普通 Lock 会死锁
        self._lock = threading.RLock()
        self._rules: list[dict] = []
        self._hits: list[dict] = []
        self._next_rule_id = 1
        self._next_hit_id = 1
        self._seen_ids: list[int] = []      # 保序(新在后),用于 FIFO 裁剪
        self._seen_set: set[int] = set()    # 与 _seen_ids 同步,做 O(1) 去重
        self._hit_tids: set[int] = set()    # 已命中过的 tid(兜底去重,从 hits 重建)
        self._initialized = False
        self._source: Optional[dict] = None
        self._poll = {"polls": 0, "last_ok_at": 0, "last_error": None, "last_error_at": 0}
        self._settings = {"window_hours": DEFAULT_WINDOW_HOURS}
        self._load()

    # ============================ 持久化 ============================

    def _load(self) -> None:
        """读取失败/文件损坏 → 从空库开始并告警(照抄 favorites 的异常元组)。

        先在局部变量里解析完再整体赋值:半途抛异常时不会留下"读了一半"的状态。
        """
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                # 顶层不是对象(被别的文件覆盖等):按损坏处理,否则 .get 会抛
                # AttributeError 逃出异常元组,把整个代理的 import 打挂
                raise ValueError("顶层不是 JSON 对象")
            rules = [r for r in (data.get("rules") or [])
                     if isinstance(r, dict) and isinstance(r.get("id"), int)]
            hits = [h for h in (data.get("hits") or [])
                    if isinstance(h, dict) and isinstance(h.get("hit_id"), int)]
            seen_ids = [int(i) for i in (data.get("seen_ids") or [])
                        if _to_positive_int(i) is not None]
            src = data.get("source")
            source = dict(src) if isinstance(src, dict) and src.get("channel_id") else None
            poll_raw = data.get("poll") or {}
            poll = {
                "polls": int(poll_raw.get("polls") or 0),
                "last_ok_at": int(poll_raw.get("last_ok_at") or 0),
                "last_error": poll_raw.get("last_error"),
                "last_error_at": int(poll_raw.get("last_error_at") or 0),
            }
            next_rule_id = int(data.get("next_rule_id") or 1)
            next_hit_id = int(data.get("next_hit_id") or 1)
            settings_raw = data.get("settings")
            if not isinstance(settings_raw, dict):
                settings_raw = {}
            window = _to_positive_int(settings_raw.get("window_hours"))
            settings = {"window_hours": window if window in WINDOW_HOURS_CHOICES
                        else DEFAULT_WINDOW_HOURS}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if os.path.exists(self.path):
                logger.warning("关注监控读取失败(%s),从空库开始: %s", self.path, exc)
            return
        self._rules = rules
        self._hits = hits
        self._seen_ids = seen_ids
        self._seen_set = set(seen_ids)
        # 兜底去重集合从命中记录重建(不单独持久化):与 hits 同生共死
        self._hit_tids = {h["tid"] for h in hits if isinstance(h.get("tid"), int)}
        self._initialized = bool(data.get("initialized"))
        self._source = source
        self._poll = poll
        self._settings = settings
        # 计数器至少要大于已加载的最大 id,否则重载后新记录会与旧记录撞 id
        self._next_rule_id = max(next_rule_id,
                                 max((r["id"] for r in rules), default=0) + 1)
        self._next_hit_id = max(next_hit_id,
                                max((h["hit_id"] for h in hits), default=0) + 1)

    def _state(self) -> dict:
        return {
            "rules": [dict(r) for r in self._rules],
            "hits": [dict(h) for h in self._hits],
            "next_rule_id": self._next_rule_id,
            "next_hit_id": self._next_hit_id,
            "seen_ids": list(self._seen_ids),
            "initialized": self._initialized,
            "source": dict(self._source) if self._source else None,
            "poll": dict(self._poll),
            "settings": dict(self._settings),
        }

    def _persist(self) -> None:
        try:
            atomic_write_json(self.path, self._state())
        except OSError as exc:
            logger.warning("关注监控落盘失败(%s): %s", self.path, exc)

    # ============================ 规则管理 ============================

    def add_rule(self, name: str, keywords: Union[str, list, tuple, None]) -> dict:
        """新增关注规则;keywords 为 str(按分隔符切分)或 list[str]。校验失败抛 ValueError。"""
        words = _normalize_keywords(keywords)
        if not words:
            raise ValueError("请至少填写一个关键字")
        if len(words) > MAX_KEYWORDS_PER_RULE:
            raise ValueError(f"单条规则最多 {MAX_KEYWORDS_PER_RULE} 个关键字(当前 {len(words)} 个)")
        for w in words:
            if len(w) > MAX_KEYWORD_LEN:
                raise ValueError(f"单个关键字最长 {MAX_KEYWORD_LEN} 个字符:{w[:MAX_KEYWORD_LEN]}…")
        with self._lock:
            if len(self._rules) >= MAX_RULES:
                raise ValueError(f"关注规则最多 {MAX_RULES} 条")
            rule = {
                "id": self._next_rule_id,
                "name": (name or "").strip(),
                "keywords": words,
                "enabled": True,
                "created_at": int(time.time()),
            }
            self._next_rule_id += 1
            self._rules.append(rule)
            self._persist()
            return dict(rule)

    def remove_rule(self, rule_id: int) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r["id"] != rule_id]
            removed = len(self._rules) != before
            if removed:
                self._persist()
            return removed

    def toggle_rule(self, rule_id: int) -> Optional[dict]:
        """翻转 enabled;规则不存在返回 None。"""
        with self._lock:
            for r in self._rules:
                if r["id"] == rule_id:
                    r["enabled"] = not r.get("enabled", True)
                    self._persist()
                    return dict(r)
        return None

    def list_rules(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._rules]

    # ============================ 匹配 ============================

    def match_title(self, title: str) -> list[dict]:
        """返回所有命中的 enabled 规则(规则内全部关键字 casefold 包含;多规则 OR)。"""
        text = (title or "").casefold()
        if not text:
            return []
        with self._lock:
            candidates = [dict(r) for r in self._rules if r.get("enabled", True)]
        matched = []
        for r in candidates:
            kws = r.get("keywords") or []
            # kws 为空说明规则损坏,不能当作"匹配一切"
            if kws and all(str(k).casefold() in text for k in kws):
                matched.append(r)
        return matched

    # ============================ 轮询写入 ============================

    def ingest(self, items: list, total_ids=None) -> tuple[int, list[dict]]:
        """把一轮信息流条目并入本地:返回 (新条目数, 新增命中列表)。

        - total_ids 非空时只保留 id ∈ total_ids 的条目(剔广告位);标题含「商家广告」
          的条目恒丢弃;
        - initialized=False 时只把 id 记入 seen_ids(建基线,不产生命中);本轮一个 id
          都没记入(全被过滤)时不置 initialized——见下方注释;
        - 已见 id 不再重复处理;命中按 tid 取 type_value(可转正整数者)否则取 id。
        """
        allowed = set(total_ids) if total_ids else None
        fresh: list[dict] = []
        recorded = 0
        hits: list[dict] = []
        with self._lock:
            baseline = not self._initialized
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                cid = _to_positive_int(it.get("id"))
                if cid is None:
                    continue
                if allowed is not None and cid not in allowed:
                    continue
                title = str(it.get("title") or "")
                if "商家广告" in title:
                    continue
                if cid in self._seen_set:
                    continue
                self._seen_ids.append(cid)
                self._seen_set.add(cid)
                recorded += 1
                if not baseline:
                    fresh.append((cid, it))
            # 以"本轮实际记入 seen_ids"为准判定是否建好基线:空响应(上游瞬时抖动)
            # 或首轮条目全被过滤(都不在 totalList / 标题含商家广告)都不能建出
            # "空基线"——否则下一轮会把满屏历史帖全当成新帖弹给用户,正是基线语义
            # 要避免的事。seen_ids 已非空(从盘上载入)时补置,保证状态自洽。
            if recorded or self._seen_ids:
                self._initialized = True
            now = int(time.time())
            window_sec = int(self._settings.get("window_hours") or DEFAULT_WINDOW_HOURS) * 3600
            for cid, it in fresh:
                if _too_old(it.get("create_time"), now, window_sec):
                    continue   # 时间窗外的旧帖只记 seen,不再提醒(防"被算法翻出"误报)
                matched = self._match_locked(str(it.get("title") or ""))
                # 再过一道"规则创建时间"门槛:规则只对创建之后发布的帖生效
                matched = [r for r in matched if _rule_eligible(it.get("create_time"), r)]
                if not matched:
                    continue
                tid = self._extract_tid(cid, it)
                # 兜底去重:该帖的命中记录还在保留期内就不再提醒。即使 seen_ids 被 FIFO
                # 裁剪、或因换源被 reset_baseline 清空,也不会重复提醒同一条帖子。
                if tid in self._hit_tids:
                    continue
                hits.append(self._make_hit_locked(cid, it, matched, tid))
            self._trim_locked()
            self._persist()
        for h in hits:
            logger.info("[watch] 命中 %s:%s", " + ".join(h["keywords"]), h["title"])
        return len(fresh), hits

    def _extract_tid(self, cid: int, it: dict) -> int:
        """命中记录的 tid:与前端 media.ts 的 threadTid 同一口径(link 里的 tid 最可信)。

        线上实测(2026-10-22):聚焦流的条目 type=6、type_value 恒为分类 id(如 '198'),
        直接采信 type_value 会把通知指向错误帖子;只有 type=2(普通帖)的 type_value
        才是真 tid。取值顺序:link 里的 tid= → type==2 的 type_value → 条目 id。
        """
        m = TID_IN_LINK_RE.search(str(it.get("link") or ""))
        if m:
            tid = _to_positive_int(m.group(1))
            if tid:
                return tid
        if _to_positive_int(it.get("type")) == 2:
            tid = _to_positive_int(it.get("type_value"))
            if tid:
                return tid
        return cid

    def _match_locked(self, title: str) -> list[dict]:
        """持锁版匹配(调用方已持 RLock)。"""
        text = title.casefold()
        if not text:
            return []
        matched = []
        for r in self._rules:
            if not r.get("enabled", True):
                continue
            kws = r.get("keywords") or []
            if kws and all(str(k).casefold() in text for k in kws):
                matched.append(r)
        return matched

    def _make_hit_locked(self, cid: int, it: dict, matched: list[dict], tid: int) -> dict:
        keywords: list[str] = []
        rule_ids: list[int] = []
        for r in matched:
            rule_ids.append(r["id"])
            for k in r.get("keywords") or []:
                if k not in keywords:   # 多规则命中时关键字去重(展示用)
                    keywords.append(k)
        hit = {
            "hit_id": self._next_hit_id,
            "tid": tid,
            "content_id": cid,
            # 发帖时间(epoch 秒;无 create_time 的源记 0):前端按它倒序,
            # 因为列表顺序是"最后活动置顶",与发帖时间不一致
            "publish_time": _to_positive_int(it.get("create_time")) or 0,
            "title": str(it.get("title") or "") or "(无标题)",
            "user_name": str(it.get("user_name") or ""),
            "publish_time_str": str(it.get("publish_time_str") or ""),
            "forum_name": str(it.get("forum_name") or ""),
            "keywords": keywords,
            "rule_ids": rule_ids,
            "created_at": int(time.time()),
        }
        self._next_hit_id += 1
        self._hits.append(hit)
        self._hit_tids.add(tid)   # 兜底去重集合随命中记录一起增长
        return dict(hit)

    def _trim_locked(self) -> None:
        """上限裁剪:hits 按 hit_id 最旧先出;seen_ids 保留最近(新在后)。"""
        while len(self._hits) > MAX_HITS:
            oldest = min(self._hits, key=lambda h: h["hit_id"])
            self._hits.remove(oldest)
            tid = oldest.get("tid")
            # 同 tid 可能有多条命中(本次兜底去重上线前的历史数据),还有记录在就不撤兜底
            if tid is not None and not any(h.get("tid") == tid for h in self._hits):
                self._hit_tids.discard(tid)
        while len(self._seen_ids) > MAX_SEEN_IDS:
            self._seen_set.discard(self._seen_ids.pop(0))

    # ============================ 查询与状态 ============================

    def list_hits(self, since: int = -1, limit: int = 100) -> list[dict]:
        """since<0:最新 limit 条(升序);since>=0:仅 hit_id>since 的前 limit 条(升序)。"""
        with self._lock:
            hits = sorted(self._hits, key=lambda h: h["hit_id"])
        limit = max(1, limit)
        if since is None or since < 0:
            return hits[-limit:]
        return [h for h in hits if h["hit_id"] > since][:limit]

    def max_hit_id(self) -> int:
        with self._lock:
            return max((h["hit_id"] for h in self._hits), default=0)

    def hit_count(self) -> int:
        with self._lock:
            return len(self._hits)

    def get_source(self) -> Optional[dict]:
        with self._lock:
            return dict(self._source) if self._source else None

    def set_source(self, src: Optional[dict]) -> None:
        with self._lock:
            self._source = dict(src) if src else None
            self._persist()

    def reset_baseline(self) -> None:
        """重建基线(换源时用):清空已见集合、回到"未初始化",规则与命中历史保留。

        换源后新源里的条目全是"没见过的",不重建基线就会把满屏旧帖当新帖弹给用户;
        hits(以及由它派生的 _hit_tids 兜底去重集合)是用户已经看过的历史记录(tid 仍
        指向有效帖子),故不清理——重置后同一帖子也不会被二次提醒。
        """
        with self._lock:
            self._seen_ids = []
            self._seen_set = set()
            self._initialized = False
            self._persist()

    def get_settings(self) -> dict:
        with self._lock:
            return dict(self._settings)

    def set_settings(self, window_hours: int) -> dict:
        """设置提醒时间窗(小时);非法值抛 ValueError(路由转 400)。"""
        if window_hours not in WINDOW_HOURS_CHOICES:
            raise ValueError("window_hours 只能是 " +
                             "/".join(str(h) for h in WINDOW_HOURS_CHOICES))
        with self._lock:
            self._settings["window_hours"] = int(window_hours)
            self._persist()
            return dict(self._settings)

    def update_poll(self, ok: bool = False, error: Optional[str] = None) -> None:
        """记录一轮轮询结果:成功刷新 last_ok_at 并清掉历史错误;失败留痕(含时间)。"""
        with self._lock:
            self._poll["polls"] = int(self._poll.get("polls") or 0) + 1
            now = int(time.time())
            if error is not None:
                self._poll["last_error"] = str(error)
                self._poll["last_error_at"] = now
            if ok:
                self._poll["last_ok_at"] = now
                self._poll["last_error"] = None
            self._persist()

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": WATCH_ENABLED,
                "interval": WATCH_INTERVAL,
                "source": dict(self._source) if self._source else None,
                "rule_count": len(self._rules),
                "hit_count": len(self._hits),
                "polls": int(self._poll.get("polls") or 0),
                "last_ok_at": int(self._poll.get("last_ok_at") or 0),
                "last_error": self._poll.get("last_error"),
                "last_error_at": int(self._poll.get("last_error_at") or 0),
                "window_hours": int(self._settings.get("window_hours") or DEFAULT_WINDOW_HOURS),
            }


store = WatchStore(os.environ.get("SDGUN_DATA_DIR") or
                   os.path.dirname(os.path.abspath(__file__)))


# ============================ 上游轮询 ============================

_poll_lock = threading.Lock()


def _call_upstream(method: str, path: str, **kwargs):
    """本模块的上游调用:绑定本模块的 upstream_client/session_store 注入缝。

    样板(身份头 + Cookie + absorb)已收敛到 sdgun_session.call_upstream;这里只把
    本模块的模块级引用显式传进去,保持既有测试对 sdgun_watch.upstream_client /
    session_store 的 monkeypatch 仍然生效。
    """
    return call_upstream(method, path, client=upstream_client,
                         session_store=session_store, **kwargs)


def _resolve_source(st: WatchStore) -> Optional[dict]:
    """解析监控源:env 指定 > 本地缓存 > 首页框架自动解析(第一个 column_type==7 频道)。

    env 三种形态:SDGUN_WATCH_FID(版块,推荐用于交易类关键字)>
    SDGUN_WATCH_CHANNEL_ID + SDGUN_WATCH_CAT_ID(频道分类)> 自动解析。
    任何失败返回 None,由调用方记 last_error、下一轮重试(自动解析只需成功一次)。
    """
    env_fid = _to_positive_int(os.environ.get("SDGUN_WATCH_FID"))
    if env_fid:
        src = {"fid": env_fid, "forum_name": "", "resolved_at": int(time.time())}
        # 同 env-channel 分支:必须落进 st.source,否则 /watch/status 恒为 null、
        # 前端脚注永远显示「解析中」;与缓存不一致才写盘(避免每轮落盘)。
        cached = st.get_source()
        if not cached or cached.get("fid") != env_fid:
            st.set_source(src)
        return src
    env_channel = _to_positive_int(os.environ.get("SDGUN_WATCH_CHANNEL_ID"))
    env_cat = _to_positive_int(os.environ.get("SDGUN_WATCH_CAT_ID"))
    if env_channel and env_cat:
        src = {"channel_id": env_channel, "cat_id": env_cat, "channel_name": "",
               "resolved_at": int(time.time())}
        # env 指定的源同样要落进 st.source:否则 /watch/status 的 source 恒为 null、
        # 前端脚注永远显示「解析中」。与缓存不一致才写盘(避免每轮落盘,env 改值自动覆盖)。
        cached = st.get_source()
        if not cached or cached.get("channel_id") != env_channel or cached.get("cat_id") != env_cat:
            st.set_source(src)
        return src
    cached = st.get_source()
    if cached:
        return cached
    try:
        r = _call_upstream("GET", MAIN_ORIGIN + "/mag/index/v2/index/pages")
        fragment = json.loads(r.json().get("index_fragment") or "[]")
        channel = next((f for f in fragment if _to_positive_int(f.get("column_type")) == 7), None)
        if not channel:
            logger.warning("[watch] 首页框架里没有 column_type=7 的频道栏目")
            return None
        channel_id = _to_positive_int(channel.get("id"))
        if not channel_id:
            return None

        r2 = _call_upstream("GET", MAIN_ORIGIN + "/mag/info/v3/channel/channelConfigById",
                            params={"channel_id": channel_id})
        data = r2.json().get("data") or {}
        info_config = [c for c in (data.get("info_config") or []) if isinstance(c, dict)]
        # 优先「新帖」类分类:线上实测频道 6 的第一个分类是「无余」(聚焦精选流,
        # type=6 混合内容 + totalList 为空 + 混入旧帖),不适合"新帖发现";
        # 「今日新帖」全 type=2、totalList=500 可过滤广告。找不到才退回第一个。
        cat = next((c for c in info_config if "新帖" in str(c.get("cat_name") or "")), None)
        cat = cat or (info_config[0] if info_config else None)
        cat_id = _to_positive_int(cat.get("cat_id")) if cat else None
        if not cat_id:
            logger.warning("[watch] 频道 %s 无可用分类(info_config 为空)", channel_id)
            return None
        src = {
            "channel_id": channel_id,
            "cat_id": cat_id,
            "channel_name": str(channel.get("name") or data.get("channel_name") or ""),
            "resolved_at": int(time.time()),
        }
        st.set_source(src)   # 解析成本高,成功即缓存,后续轮询直接用
        logger.info("[watch] 监控源已解析:%s(频道 %s 分类 %s/%s)",
                    src["channel_name"] or "未命名频道", channel_id, cat_id,
                    cat.get("cat_name") or "未命名")
        return src
    except (upstream_lib.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
        logger.warning("[watch] 监控源解析失败(下轮重试): %s: %s", type(exc).__name__, exc)
        return None


def _normalize_thread_item(t: dict) -> dict:
    """版块 threadList 条目 → ingest 认的信息流条目形态(字段名完全不同)。

    create_time(epoch 秒字符串,线上实测存在且精确)原样透传:ingest 用它做时间窗
    过滤(过滤被翻出的旧帖);频道源条目没有该字段,ingest 走 fail-open 放行。
    """
    tid = _to_positive_int(t.get("tid"))
    return {
        "id": tid,
        "type": 2,                                   # threadList 全是普通帖
        "type_value": str(tid) if tid else "",
        "title": str(t.get("subject") or ""),        # subject 才是纯文本标题
        "user_name": str(t.get("user_name") or ""),
        "publish_time_str": str(t.get("dateline") or ""),
        "forum_name": str(t.get("forum_name") or ""),
        "create_time": str(t.get("create_time") or ""),
    }


def _fetch_feed(source: dict) -> tuple[list, Optional[set]]:
    """拉监控源第一页;返回 (条目数组, totalList 集合或 None)。

    - fid 源(版块 threadList):频道信息流是**策展视图**会漏新帖(2026-10-22 实测
      玩具交易区 7/11 分钟前的新帖不在 cat3 p=1 的 50 条里,而版块 threadList
      fid=176 前 50 条就包含),故交易类关键字应走版块源。该接口没有 totalList,
      置顶旧帖交给 seen_ids 去重自然吸收(首轮只建基线,不会误报)。
    - channel/cat 源:v2 频道流,用 totalList 过滤广告位。

    RequestException/ValueError 向上抛(poll_once 统一兜住并记 last_error)。
    """
    fid = _to_positive_int(source.get("fid"))
    if fid:
        r = _call_upstream("GET", MAIN_ORIGIN + "/mag/circle/v1/Forum/threadList",
                           params={"fid": fid, "p": 1, "step": 50})
        payload = r.json()
        items = [_normalize_thread_item(t) for t in (payload.get("list") or [])
                 if isinstance(t, dict)]
        return items, None

    r = _call_upstream("GET", MAIN_ORIGIN + "/mag/info/v2/channel/infoListByCatId",
                       params={"channel_id": source["channel_id"], "cat_id": source["cat_id"],
                               "p": 1, "step": 50, "tab": 0, "is_app_first": -1})
    payload = r.json()
    items = payload.get("list") or []
    raw_total = payload.get("totalList")
    total_ids: Optional[set] = None
    if raw_total:
        total_ids = set()
        for i in raw_total:
            n = _to_positive_int(i)
            if n is not None:
                total_ids.add(n)
    return items, total_ids


def _target_key(src: Optional[dict]) -> Optional[tuple]:
    """源的"实际轮询目标"指纹(换源判定用)。

    不能直接比整个 dict:resolved_at 每轮都变。fid 与 channel+cat 是两种不同的
    拉取口径,必须视为不同目标。
    """
    if not src:
        return None
    fid = _to_positive_int(src.get("fid"))
    if fid:
        return ("fid", fid)
    channel = _to_positive_int(src.get("channel_id"))
    cat = _to_positive_int(src.get("cat_id"))
    if channel and cat:
        return ("cat", channel, cat)
    return None


def poll_once(st: WatchStore, fetch=None, resolve=None) -> dict:
    """执行一轮"解析源 → 拉流 → 并入本地",返回结构化结果(异常不外抛)。

    fetch/resolve 可注入(测试离线跑);_poll_lock 防并发——手动「立即检测」撞上
    后台轮询时直接返回提示,而不是排队等一次完整的网络往返。
    """
    fetch = fetch or _fetch_feed
    resolve = resolve or _resolve_source
    if not _poll_lock.acquire(blocking=False):
        return {"ok": False, "new_items": 0, "hits_added": 0,
                "error": "上一次检测尚未结束,请稍后再试"}
    try:
        # 旧目标必须在 resolve 之前读:_resolve_source 命中 env/自动解析时会先把新
        # source 落盘,之后再比较就永远相等,换源不会重建基线(会刷屏)。
        prev_target = _target_key(st.get_source())
        source = resolve(st)
        if not source:
            error = "监控源解析失败(将在下一轮自动重试)"
            st.update_poll(error=error)
            return {"ok": False, "new_items": 0, "hits_added": 0, "error": error}
        target = _target_key(source)
        if prev_target != target:
            # 换源(或首次解析):静默重建基线,不把新源里满屏的旧帖当新帖弹给用户
            logger.info("[watch] 监控源变化 %s → %s,重建基线(清空已见集合)",
                        prev_target, target)
            st.reset_baseline()
        if _target_key(st.get_source()) != target:
            st.set_source(source)   # 兜底落盘(resolve 被注入时不会自己写)
        items, total_ids = fetch(source)
        new_items, hits = st.ingest(items, total_ids)
        st.update_poll(ok=True)
        return {"ok": True, "new_items": new_items, "hits_added": len(hits), "error": None}
    except Exception as exc:  # noqa: BLE001 —— 单轮失败不能中断守护线程
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("[watch] 检测失败: %s", error)
        st.update_poll(error=error)
        return {"ok": False, "new_items": 0, "hits_added": 0, "error": error}
    finally:
        _poll_lock.release()


# ============================ 守护线程 ============================

def _parse_int(raw, default: int, what: str) -> int:
    """配置解析失败一律回退默认并告警(守护线程不能死于配置笔误,同 sdgun_sign)。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 不是合法整数,回退默认 %d", what, raw, default)
        return default


_RAW_INTERVAL = _parse_int(os.environ.get("SDGUN_WATCH_INTERVAL", "60"), 60,
                           "SDGUN_WATCH_INTERVAL")
WATCH_ENABLED = os.environ.get("SDGUN_WATCH", "1") != "0" and _RAW_INTERVAL != 0
WATCH_INTERVAL = min(3600, max(20, _RAW_INTERVAL))

_started = False
_start_lock = threading.Lock()


def _watch_loop() -> None:
    while True:
        try:
            res = poll_once(store)
            if not res.get("ok"):
                logger.warning("[watch] 本轮检测未成功: %s", res.get("error"))
        except Exception as exc:  # noqa: BLE001 —— 守护线程不能死
            logger.warning("[watch] 轮询异常: %s", exc)
        time.sleep(WATCH_INTERVAL)


def start_watcher() -> None:
    """启动关注监控守护线程(由代理 lifespan 调用;幂等,重复调用不重复起线程)。"""
    global _started
    if not WATCH_ENABLED:
        logger.info("[watch] 关注监控已关闭(SDGUN_WATCH=0 或 SDGUN_WATCH_INTERVAL=0)")
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_watch_loop, daemon=True, name="watch").start()
    logger.info("[watch] 关注监控已启动:每 %d 秒轮询一次(首次只建立基线,不回溯历史帖)",
                WATCH_INTERVAL)


# ============================ 路由 ============================

router = APIRouter()


class RuleBody(BaseModel):
    """新增规则:keywords 允许字符串(逗号/顿号/空格分隔)或字符串数组。"""
    name: str = ""
    keywords: Union[str, list[str]]


class RuleIdBody(BaseModel):
    id: int


class SettingsBody(BaseModel):
    window_hours: int


@router.get("/watch/status")
async def watch_status():
    """关注监控状态:开关/间隔/监控源/计数/最近轮询结果。"""
    return store.status()


@router.get("/watch/rules")
async def watch_rules():
    return {"rules": store.list_rules()}


@router.post("/watch/rules")
async def watch_rules_add(body: RuleBody):
    """新增规则(校验失败 400,msg 直接给前端展示)。"""
    try:
        rule = store.add_rule(body.name, body.keywords)
    except ValueError as exc:
        return JSONResponse({"msg": str(exc)}, status_code=400)
    return {"rule": rule, "rules": store.list_rules()}


@router.post("/watch/rules/remove")
async def watch_rules_remove(body: RuleIdBody):
    return {"removed": store.remove_rule(body.id), "rules": store.list_rules()}


@router.post("/watch/rules/toggle")
async def watch_rules_toggle(body: RuleIdBody):
    """启用/停用某条规则(不删除,便于临时静音)。"""
    return {"rule": store.toggle_rule(body.id), "rules": store.list_rules()}


@router.get("/watch/hits")
async def watch_hits(since: int = -1, limit: int = 100):
    """命中记录:since<0 取最新 limit 条;since>=0 只取更新的(前端游标用)。"""
    return {
        "hits": store.list_hits(since=since, limit=limit),
        "max_hit_id": store.max_hit_id(),
        "total": store.hit_count(),
    }


@router.get("/watch/settings")
async def watch_settings_get():
    """监控设置(当前只有提醒时间窗)。"""
    return store.get_settings()


@router.post("/watch/settings")
async def watch_settings_set(body: SettingsBody):
    """改设置;非法 window_hours 400(只接受 12/24/48)。"""
    try:
        return store.set_settings(body.window_hours)
    except ValueError as exc:
        return JSONResponse({"msg": str(exc)}, status_code=400)


@router.post("/watch/run")
async def watch_run():
    """立即检测一次(阻塞式,放线程池,避免网络等待卡住事件循环)。"""
    return await run_in_threadpool(poll_once, store)
