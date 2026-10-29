# -*- coding: utf-8 -*-
"""关注关键字监控(WatchStore + /watch 路由)的单元测试(离线)。

上游轮询全部用注入的假 fetch/resolve(或 monkeypatch poll_once),不发真实网络请求。
"""

import json
import tempfile
import time

import pytest

import sdgun_watch
from sdgun_watch import WatchStore


def _item(cid, title, **kw):
    """构造一条信息流条目(字段集与上游 v2 频道流条目一致)。"""
    it = {"id": cid, "type": 2, "type_value": str(cid), "title": title,
          "user_name": "卖家", "publish_time_str": "1小时前", "forum_name": "玩具交易区"}
    it.update(kw)
    return it


class TestWatchStore:
    # ---------------------- 规则归一化与校验 ----------------------

    def test_add_rule_normalizes_string_keywords(self, tmp_path):
        s = WatchStore(str(tmp_path))
        rule = s.add_rule("", "出,Glock  glock、AK")
        # 中英文逗号/顿号/空格切分 + strip + casefold 去重(保留首次形态)
        assert rule["keywords"] == ["出", "Glock", "AK"]
        assert rule["enabled"] is True
        assert rule["name"] == ""
        assert s.list_rules()[0]["id"] == rule["id"]

    def test_add_rule_accepts_list(self, tmp_path):
        s = WatchStore(str(tmp_path))
        rule = s.add_rule("交易", ["出", " glock ", ""])
        assert rule["keywords"] == ["出", "glock"]
        assert rule["name"] == "交易"

    def test_add_rule_rejects_too_many_keywords(self, tmp_path):
        s = WatchStore(str(tmp_path))
        too_many = " ".join(f"k{i}" for i in range(sdgun_watch.MAX_KEYWORDS_PER_RULE + 1))
        with pytest.raises(ValueError):
            s.add_rule("", too_many)

    def test_add_rule_rejects_long_keyword(self, tmp_path):
        s = WatchStore(str(tmp_path))
        with pytest.raises(ValueError):
            s.add_rule("", "x" * (sdgun_watch.MAX_KEYWORD_LEN + 1))

    def test_add_rule_rejects_empty_keywords(self, tmp_path):
        s = WatchStore(str(tmp_path))
        with pytest.raises(ValueError):
            s.add_rule("", " , 、  ")

    def test_add_rule_rejects_too_many_rules(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sdgun_watch, "MAX_RULES", 3)
        s = WatchStore(str(tmp_path))
        for i in range(3):
            s.add_rule("", f"k{i}")
        with pytest.raises(ValueError):
            s.add_rule("", "k9")

    def test_remove_and_toggle_roundtrip(self, tmp_path):
        s = WatchStore(str(tmp_path))
        rid = s.add_rule("", "出")["id"]
        assert s.toggle_rule(rid)["enabled"] is False
        assert s.toggle_rule(rid)["enabled"] is True
        assert s.toggle_rule(999) is None
        assert s.remove_rule(rid) is True
        assert s.remove_rule(rid) is False
        assert s.list_rules() == []

    # ---------------------- 匹配语义 ----------------------

    def test_match_single_keyword_containment(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "glock")
        assert s.match_title("出售 GLOCK 17")[0]["keywords"] == ["glock"]

    def test_match_requires_all_keywords(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", ["出", "glock"])
        assert s.match_title("出售 glock 17")      # 全中
        assert s.match_title("出售 ak47") == []    # 缺一不中
        assert s.match_title("求 glock") == []

    def test_match_is_case_insensitive(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "GLOCK")
        assert s.match_title("出 glock 17")

    def test_match_multi_rule_or(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "glock")
        rid2 = s.add_rule("", "1911")["id"]
        assert [r["id"] for r in s.match_title("出 1911 军版")] == [rid2]

    def test_disabled_rule_not_matched(self, tmp_path):
        s = WatchStore(str(tmp_path))
        rid = s.add_rule("", "glock")["id"]
        s.toggle_rule(rid)
        assert s.match_title("出 glock") == []

    # ---------------------- 基线语义与 ingest ----------------------

    def test_first_ingest_builds_baseline_without_hits(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        new, hits = s.ingest([_item(1, "出 Glock"), _item(2, "出 AK")], None)
        assert (new, hits) == (0, [])      # 首次只建基线,不回溯历史帖
        assert s.max_hit_id() == 0
        assert s.ingest([_item(1, "出 Glock")], None) == (0, [])   # 已记入 seen

    def test_new_item_after_baseline_produces_hit(self, tmp_path):
        s = WatchStore(str(tmp_path))
        rid = s.add_rule("", ["出", "glock"])["id"]
        s.ingest([_item(1, "基线帖")], None)
        new, hits = s.ingest([_item(1, "基线帖"), _item(2, "出 Glock 17 成色好")], None)
        assert new == 1
        assert len(hits) == 1
        h = hits[0]
        assert (h["hit_id"], h["tid"], h["content_id"], h["rule_ids"]) == (1, 2, 2, [rid])
        assert h["keywords"] == ["出", "glock"]
        assert h["title"] == "出 Glock 17 成色好"

    def test_hit_tid_prefers_type_value_and_falls_back_to_id(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        _, hits = s.ingest([_item(7, "出 沙漠之鹰", type_value="abc"),
                            _item(8, "出 1911", type_value="8008")], None)
        assert [(h["tid"], h["content_id"]) for h in hits] == [(7, 7), (8008, 8)]

    def test_hit_tid_taken_from_link(self, tmp_path):
        """link 里的 tid 最可信(与前端 media.threadTid 同口径),优先于 type_value。"""
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        _, hits = s.ingest([_item(200, "出 Glock", type=2, type_value="999",
                                 link="/mag/circle/v1/forum/threadViewPage?tid=4376213&reply_id=1")], None)
        assert hits[0]["tid"] == 4376213

    def test_hit_tid_ignores_category_type_value(self, tmp_path):
        """聚焦流实测:type=6 的 type_value 是分类 id('198'),必须回退到条目 id。"""
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        _, hits = s.ingest([_item(777, "出 聚焦精选", type=6, type_value="198", link="")], None)
        assert hits[0]["tid"] == 777

    def test_hit_tid_taken_from_type_value_for_normal_thread(self, tmp_path):
        """今日新帖流实测:type=2 且 type_value 即真实 tid。"""
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        _, hits = s.ingest([_item(200, "出 Glock", type=2, type_value="4376213", link="")], None)
        assert hits[0]["tid"] == 4376213

    def test_repeat_ingest_does_not_duplicate(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(1, "基线")], None)
        batch = [_item(2, "出 Glock")]
        assert s.ingest(batch, None)[0] == 1
        assert s.ingest(batch, None) == (0, [])
        assert s.hit_count() == 1

    def test_empty_first_response_does_not_build_empty_baseline(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        assert s.ingest([], None) == (0, [])                        # 上游瞬时返回空列表
        assert s.ingest([_item(1, "出 Glock")], None) == (0, [])    # 下一轮才建基线,不误报
        assert s.ingest([_item(2, "出 AK")], None)[1][0]["title"] == "出 AK"

    def test_all_filtered_first_round_does_not_build_empty_baseline(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        # 首轮条目全被广告过滤(id 不在 totalList / 标题含商家广告)→ 不能建空基线
        assert s.ingest([_item(1, "出 Glock"), _item(2, "出 AK【商家广告】")], {999}) == (0, [])
        assert s._initialized is False
        assert s._seen_ids == []
        # 第二轮正常条目(含命中帖)仍按基线处理:只记 seen,0 命中
        assert s.ingest([_item(1, "出 Glock"), _item(2, "出 AK")], {1, 2}) == (0, [])
        assert s._initialized is True
        # 第三轮出现新命中帖 → 1 命中
        new, hits = s.ingest([_item(3, "出 沙漠之鹰")], {1, 2, 3})
        assert (new, len(hits)) == (1, 1)
        assert hits[0]["tid"] == 3

    # ---------------------- 广告过滤 ----------------------

    def test_total_ids_filter_drops_items_outside(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.ingest([_item(1, "基线")], None)
        new, _hits = s.ingest([_item(2, "出 Glock"), _item(3, "广告位条目")], {2})
        assert new == 1                     # id=3 不在 totalList → 丢弃

    def test_ad_title_always_dropped(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(1, "基线")], None)
        assert s.ingest([_item(2, "出 Glock【商家广告】")], None) == (0, [])

    # ---------------------- list_hits 游标 ----------------------

    def test_list_hits_latest_and_cursor(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        for i in range(1, 4):
            s.ingest([_item(100 + i, f"出 第{i}条")], None)
        latest = s.list_hits(-1, 2)                       # 最新 2 条,升序
        assert [h["hit_id"] for h in latest] == [2, 3]
        assert [h["hit_id"] for h in s.list_hits(latest[0]["hit_id"], 10)] == [3]
        assert s.list_hits(3, 10) == []
        assert s.max_hit_id() == 3
        assert s.hit_count() == 3

    # ---------------------- 按 tid 的兜底去重 ----------------------

    def test_hit_tid_dedup_when_seen_ids_lost(self, tmp_path):
        """seen_ids 被裁剪 / 换源重置后,同一帖子仍不二次提醒(命中记录还在保留期)。"""
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        # 规则创建时间往前挪 1 小时:本用例测 tid 兜底,别被"规则创建门槛"挡掉
        s._rules[0]["created_at"] = int(time.time()) - 3600
        s.ingest([_item(100, "基线")], None)
        post = _item(1, "出 Glock 17", create_time=str(int(time.time()) - 60))
        assert len(s.ingest([post], None)[1]) == 1

        # a) 模拟 seen_ids 被 FIFO 挤掉(基线仍在):算新条目,但不产生第二条命中
        s._seen_ids = []
        s._seen_set = set()
        assert s.ingest([post], None) == (1, [])
        assert s.hit_count() == 1

        # b) 模拟换源重置基线(seen 清空 + initialized=False):同样不二次命中
        s.reset_baseline()
        assert s.ingest([post], None) == (0, [])
        assert s.hit_count() == 1
        assert s._hit_tids == {1}

    def test_tid_dedup_released_after_hit_trimmed(self, tmp_path, monkeypatch):
        """边界语义:命中记录被 MAX_HITS 裁掉后,该 tid 才允许重新命中。"""
        monkeypatch.setattr(sdgun_watch, "MAX_HITS", 2)
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        post = _item(1, "出 Glock 17")
        assert len(s.ingest([post], None)[1]) == 1
        s.ingest([_item(2, "出 AK"), _item(3, "出 1911")], None)   # 把 tid=1 挤出保留期
        assert s.hit_count() == 2
        assert 1 not in s._hit_tids

        s._seen_ids = []
        s._seen_set = set()
        assert len(s.ingest([post], None)[1]) == 1                 # 可以重新命中

    def test_hit_tids_rebuilt_on_reload(self, tmp_path):
        s1 = WatchStore(str(tmp_path))
        s1.add_rule("", "出")
        s1.ingest([_item(100, "基线")], None)
        s1.ingest([_item(1, "出 Glock")], None)

        s2 = WatchStore(str(tmp_path))
        assert s2._hit_tids == {1}          # 从已加载 hits 重建(不单独持久化)
        s2._seen_ids = []
        s2._seen_set = set()
        assert s2.ingest([_item(1, "出 Glock")], None) == (1, [])

    def test_different_tids_still_both_hit(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        _, hits = s.ingest([_item(1, "出 Glock"), _item(2, "出 AK")], None)
        assert [h["tid"] for h in hits] == [1, 2]   # 兜底只拦重复 tid,不误伤新帖

    # ---------------------- 上限裁剪 ----------------------

    def test_hits_cap_trims_oldest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sdgun_watch, "MAX_HITS", 2)
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        for i in range(1, 4):
            s.ingest([_item(100 + i, f"出 {i}")], None)
        assert [h["hit_id"] for h in s.list_hits(-1, 10)] == [2, 3]

    def test_seen_ids_cap_keeps_recent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sdgun_watch, "MAX_SEEN_IDS", 3)
        s = WatchStore(str(tmp_path))
        s.ingest([_item(i, f"帖{i}") for i in range(1, 6)], None)
        assert s._seen_ids == [3, 4, 5]
        # 被裁掉的 id 再次出现会当成新条目(上限换空间的已知代价)
        assert s.ingest([_item(1, "帖1")], None)[0] == 1

    # ---------------------- 持久化 ----------------------

    def test_persist_and_reload(self, tmp_path):
        s1 = WatchStore(str(tmp_path))
        rid = s1.add_rule("交易", "出,glock")["id"]
        s1.ingest([_item(100, "基线")], None)
        s1.ingest([_item(101, "出 Glock 17")], None)
        s1.update_poll(ok=True)

        s2 = WatchStore(str(tmp_path))
        assert s2.list_rules()[0]["id"] == rid
        assert s2.list_rules()[0]["keywords"] == ["出", "glock"]
        assert s2.list_rules()[0]["name"] == "交易"
        assert s2.hit_count() == 1
        assert s2.list_hits(-1, 5)[0]["tid"] == 101
        assert s2.status()["last_ok_at"] > 0
        # seen/基线状态保留:重载后同一批不会重复产生命中
        assert s2.ingest([_item(101, "出 Glock 17")], None) == (0, [])
        # 新规则 id 不与已加载的撞号
        assert s2.add_rule("", "ak")["id"] == rid + 1

    def test_corrupt_file_starts_empty(self, tmp_path):
        (tmp_path / "sdgun_watch.json").write_text("{bad", encoding="utf-8")
        s = WatchStore(str(tmp_path))
        assert s.list_rules() == []
        assert s.hit_count() == 0
        assert s.status()["source"] is None


class TestPollOnce:
    """poll_once 是"解析源 → 拉流 → 并入本地"的可测单元(fetch/resolve 注入)。"""

    def test_injected_fetch_builds_baseline_then_hits(self, tmp_path):
        st = WatchStore(str(tmp_path))
        st.add_rule("", "出")
        batches = [[_item(1, "出 Glock")],
                   [_item(1, "出 Glock"), _item(2, "出 Glock 17 军版")]]
        seen_sources = []

        def fetch(source):
            seen_sources.append(source)
            return batches.pop(0), {1, 2}

        def resolve(_st):
            return {"channel_id": 6, "cat_id": 3, "channel_name": "玩具交易区"}

        r1 = sdgun_watch.poll_once(st, fetch=fetch, resolve=resolve)
        assert (r1["ok"], r1["new_items"], r1["hits_added"]) == (True, 0, 0)   # 首轮建基线
        r2 = sdgun_watch.poll_once(st, fetch=fetch, resolve=resolve)
        assert (r2["ok"], r2["new_items"], r2["hits_added"]) == (True, 1, 1)
        assert st.list_hits(-1, 5)[0]["title"] == "出 Glock 17 军版"
        assert seen_sources[0]["channel_id"] == 6
        assert st.status()["polls"] == 2
        assert st.status()["last_ok_at"] > 0

    def test_fetch_error_recorded_without_raising(self, tmp_path):
        st = WatchStore(str(tmp_path))

        def fetch(_source):
            raise ValueError("上游返回非 JSON")

        r = sdgun_watch.poll_once(st, fetch=fetch,
                                  resolve=lambda _s: {"channel_id": 1, "cat_id": 2})
        assert r["ok"] is False
        assert "ValueError" in r["error"]
        assert st.status()["last_error"]
        assert st.status()["last_error_at"] > 0

    def test_unresolved_source_reports_error(self, tmp_path):
        st = WatchStore(str(tmp_path))
        r = sdgun_watch.poll_once(st, fetch=lambda _s: ([], None), resolve=lambda _s: None)
        assert r["ok"] is False
        assert r["error"]


class _NoNetworkClient:
    """env 指定监控源时不该有任何上游调用;一旦被调用就大声失败。"""

    def get(self, *_args, **_kwargs):
        raise AssertionError("env 指定监控源时不应发起网络请求")


class TestResolveSourceEnv:
    def test_env_fid_wins_over_channel_cat_and_skips_network(self, tmp_path, monkeypatch):
        """SDGUN_WATCH_FID 优先级最高:同时设了 channel/cat 也走版块源,且零网络。"""
        st = WatchStore(str(tmp_path))
        monkeypatch.setenv("SDGUN_WATCH_FID", "176")
        monkeypatch.setenv("SDGUN_WATCH_CHANNEL_ID", "6")
        monkeypatch.setenv("SDGUN_WATCH_CAT_ID", "2")
        monkeypatch.setattr(sdgun_watch, "upstream_client", _NoNetworkClient())
        calls = []
        orig_set_source = st.set_source
        monkeypatch.setattr(st, "set_source",
                            lambda src: (calls.append(src), orig_set_source(src))[1])

        src = sdgun_watch._resolve_source(st)
        assert (src["fid"], src["forum_name"]) == (176, "")
        assert set(src) == {"fid", "forum_name", "resolved_at"}
        assert st.get_source()["fid"] == 176
        assert len(calls) == 1
        # 值没变 → 不重复落盘
        assert sdgun_watch._resolve_source(st)["fid"] == 176
        assert len(calls) == 1
        # fid 改值 → 覆盖缓存
        monkeypatch.setenv("SDGUN_WATCH_FID", "177")
        assert sdgun_watch._resolve_source(st)["fid"] == 177
        assert st.get_source()["fid"] == 177
        assert len(calls) == 2

    def test_env_source_skips_network_and_persists(self, tmp_path, monkeypatch):
        st = WatchStore(str(tmp_path))
        monkeypatch.delenv("SDGUN_WATCH_FID", raising=False)
        monkeypatch.setenv("SDGUN_WATCH_CHANNEL_ID", "6")
        monkeypatch.setenv("SDGUN_WATCH_CAT_ID", "3")
        monkeypatch.setattr(sdgun_watch, "upstream_client", _NoNetworkClient())
        calls = []
        orig_set_source = st.set_source
        monkeypatch.setattr(st, "set_source",
                            lambda src: (calls.append(src), orig_set_source(src))[1])

        src = sdgun_watch._resolve_source(st)
        assert (src["channel_id"], src["cat_id"]) == (6, 3)
        # source 必须落进 store,否则 /watch/status 恒为 null、前端脚注恒显「解析中」
        assert (st.get_source()["channel_id"], st.get_source()["cat_id"]) == (6, 3)
        assert len(calls) == 1

        # 第二轮:值没变 → 不重复落盘
        assert sdgun_watch._resolve_source(st)["channel_id"] == 6
        assert len(calls) == 1

        # env 改值 → 自动覆盖缓存
        monkeypatch.setenv("SDGUN_WATCH_CAT_ID", "4")
        assert sdgun_watch._resolve_source(st)["cat_id"] == 4
        assert st.get_source()["cat_id"] == 4
        assert len(calls) == 2


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSessionStore:
    """替掉 sdgun_session.store:断言上游响应被 absorb,且不碰真实 Cookie。"""

    def __init__(self):
        self.absorbed = 0

    def cookie_header(self):
        return "sid=test"

    def absorb(self, _resp):
        self.absorbed += 1
        return False


class _FakeUpstreamClient:
    """按调用顺序吐出预置响应,并记录每次请求的 (url, params)。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        return _FakeResp(self._responses.pop(0))


def _fragment(*tabs):
    import json
    return {"index_fragment": json.dumps(list(tabs))}


class TestResolveSourceAuto:
    """自动解析监控源(离线):index/pages → 第一个 column_type=7 频道 → channelConfig。"""

    def _patch(self, monkeypatch, responses):
        monkeypatch.delenv("SDGUN_WATCH_FID", raising=False)
        monkeypatch.delenv("SDGUN_WATCH_CHANNEL_ID", raising=False)
        monkeypatch.delenv("SDGUN_WATCH_CAT_ID", raising=False)
        client = _FakeUpstreamClient(responses)
        session = _FakeSessionStore()
        monkeypatch.setattr(sdgun_watch, "upstream_client", client)
        monkeypatch.setattr(sdgun_watch, "session_store", session)
        return client, session

    def test_prefers_cat_named_xintie(self, tmp_path, monkeypatch):
        """频道 6 的 info_config 里「今日新帖」优先于第一个分类「无余」(上线实测)。"""
        st = WatchStore(str(tmp_path))
        pages = _fragment({"id": "1", "name": "推荐", "column_type": 1},
                          {"id": "6", "name": "玩具交易区", "column_type": 7})
        config = {"data": {"channel_name": "玩具交易区", "info_config": [
            {"cat_id": 1, "cat_name": "无余"},
            {"cat_id": 2, "cat_name": "今日新帖"},
        ]}}
        client, session = self._patch(monkeypatch, [pages, config])

        src = sdgun_watch._resolve_source(st)
        assert (src["channel_id"], src["cat_id"]) == (6, 2)
        assert src["channel_name"] == "玩具交易区"
        assert st.get_source()["cat_id"] == 2          # 已缓存,后续轮询不再解析
        assert [c[1] for c in client.calls] == [None, {"channel_id": 6}]
        assert session.absorbed == 2

    def test_falls_back_to_first_cat_without_xintie(self, tmp_path, monkeypatch):
        st = WatchStore(str(tmp_path))
        pages = _fragment({"id": "6", "name": "玩具交易区", "column_type": 7})
        config = {"data": {"info_config": [{"cat_id": 1, "cat_name": "无余"},
                                           {"cat_id": 9, "cat_name": "关注"}]}}
        self._patch(monkeypatch, [pages, config])

        src = sdgun_watch._resolve_source(st)
        assert src["cat_id"] == 1
        assert src["channel_name"] == "玩具交易区"      # 无 channel_name 时回落到栏目名


class TestFetchFeed:
    """_fetch_feed 的两种源形态(离线,注入假 upstream_client/session_store)。"""

    def _patch(self, monkeypatch, payload):
        client = _FakeUpstreamClient([payload])
        monkeypatch.setattr(sdgun_watch, "upstream_client", client)
        monkeypatch.setattr(sdgun_watch, "session_store", _FakeSessionStore())
        return client

    def test_forum_source_normalizes_thread_list_items(self, monkeypatch):
        # threadList 条目字段名与频道流完全不同,必须归一化成 ingest 认的形态
        client = self._patch(monkeypatch, {"list": [{
            "tid": "4376213", "subject": "出 Glock 17 军版", "user_name": "卖家",
            "dateline": "7分钟前", "forum_name": "玩具交易区",
            "title": '<span>出 Glock 17 军版</span>', "is_top": 1,
            "create_time": "1792665000",
        }]})
        items, total_ids = sdgun_watch._fetch_feed({"fid": 176, "forum_name": ""})
        assert total_ids is None                       # 该接口无 totalList
        assert client.calls[0][0].endswith("/mag/circle/v1/Forum/threadList")
        assert client.calls[0][1] == {"fid": 176, "p": 1, "step": 50}
        assert items == [{"id": 4376213, "type": 2, "type_value": "4376213",
                          "title": "出 Glock 17 军版", "user_name": "卖家",
                          "publish_time_str": "7分钟前", "forum_name": "玩具交易区",
                          "create_time": "1792665000"}]   # create_time 透传(时间窗用)

    def test_forum_source_drops_items_without_valid_tid(self, monkeypatch):
        self._patch(monkeypatch, {"list": [{"tid": "abc", "subject": "坏条目"},
                                          {"tid": "12", "subject": "好条目"}, "not-a-dict"]})
        items, _total = sdgun_watch._fetch_feed({"fid": 176})
        assert [i["id"] for i in items] == [None, 12]   # 非 dict 条目直接丢,id 无效者留给 ingest
        st = WatchStore(tempfile.mkdtemp())
        assert st.ingest(items, None) == (0, [])        # id 无效者被 ingest 忽略

    def test_forum_pinned_old_post_absorbed_by_seen(self, monkeypatch, tmp_path):
        """置顶旧帖没有 totalList 可过滤,靠 seen_ids 去重自然吸收。"""
        pinned = {"tid": "100", "subject": "出 置顶旧帖", "user_name": "版主",
                  "dateline": "2020-01-01", "forum_name": "玩具交易区"}
        fresh = {"tid": "4376213", "subject": "出 Glock 17", "user_name": "卖家",
                 "dateline": "7分钟前", "forum_name": "玩具交易区"}
        self._patch(monkeypatch, {"list": [pinned, fresh]})
        items, _total = sdgun_watch._fetch_feed({"fid": 176})

        st = WatchStore(str(tmp_path))
        st.add_rule("", "出")
        assert st.ingest(items, None) == (0, [])        # 首轮:置顶旧帖也进 seen,不误报
        assert st.ingest(items, None) == (0, [])        # 再拉一次:仍不误报
        assert st.hit_count() == 0
        # 出现真正的新帖才命中
        new_items, hits = st.ingest(items + [{"id": 999, "type": 2, "type_value": "999",
                                              "title": "出 1911", "user_name": "卖家",
                                              "publish_time_str": "1分钟前",
                                              "forum_name": "玩具交易区"}], None)
        assert (new_items, len(hits)) == (1, 1)


class TestBaselineResetOnSourceSwitch:
    def test_source_switch_rebuilds_baseline(self, tmp_path):
        st = WatchStore(str(tmp_path))
        st.add_rule("", "出")
        src_a = {"channel_id": 6, "cat_id": 2, "channel_name": "玩具交易区"}
        src_b = {"fid": 176, "forum_name": "玩具交易区"}
        old_posts = [_item(1, "出 Glock"), _item(2, "出 AK")]

        # 源 A 首轮:建基线(0 命中)
        r = sdgun_watch.poll_once(st, fetch=lambda _s: (old_posts, {1, 2}),
                                  resolve=lambda _s: src_a)
        assert (r["ok"], r["new_items"], r["hits_added"]) == (True, 0, 0)
        assert st.get_source()["cat_id"] == 2

        # 换到源 B:基线被重置 → 满屏"新源旧帖"当基线吸收,当轮 0 命中
        r = sdgun_watch.poll_once(st, fetch=lambda _s: (old_posts, None),
                                  resolve=lambda _s: src_b)
        assert (r["ok"], r["new_items"], r["hits_added"]) == (True, 0, 0)
        assert st.get_source()["fid"] == 176
        # 源 B 第二轮:同源不重置,新帖才命中
        r = sdgun_watch.poll_once(st, fetch=lambda _s: (old_posts + [_item(3, "出 1911")], None),
                                  resolve=lambda _s: src_b)
        assert (r["new_items"], r["hits_added"]) == (1, 1)

        # 换回源 A:同样重置(用空响应观察 initialized 确实回到 False)
        r = sdgun_watch.poll_once(st, fetch=lambda _s: ([], {1, 2}), resolve=lambda _s: src_a)
        assert (r["new_items"], r["hits_added"]) == (0, 0)
        assert st._initialized is False
        assert st.get_source()["cat_id"] == 2
        # 规则与命中历史保留
        assert len(st.list_rules()) == 1
        assert st.hit_count() == 1

    def test_reset_baseline_keeps_rules_and_hits(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.ingest([_item(100, "基线")], None)
        s.ingest([_item(101, "出 Glock")], None)
        assert s.hit_count() == 1
        s.reset_baseline()
        assert s._initialized is False
        assert s._seen_ids == []
        assert s.hit_count() == 1                      # 命中历史保留
        assert [r["keywords"] for r in s.list_rules()] == [["出"]]
        # 重置后重新建基线:满屏旧帖不误报
        assert s.ingest([_item(101, "出 Glock"), _item(102, "出 AK")], None) == (0, [])
        assert s.hit_count() == 1


class TestWindowHours:
    """提醒时间窗:只提醒"发布时间在 N 小时内"的帖子(压掉被翻出的旧帖误报)。"""

    def _store(self, tmp_path):
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        # 把规则创建时间往前挪:本类只测时间窗,不让"规则创建门槛"(见 TestRuleCreationGate)混进来
        s._rules[0]["created_at"] = int(time.time()) - 100 * 3600
        s.ingest([_item(100, "基线")], None)
        return s

    def test_hit_within_window(self, tmp_path):
        s = self._store(tmp_path)
        now = int(time.time())
        new_items, hits = s.ingest([_item(1, "出 Glock 新帖",
                                          create_time=str(now - 3600))], None)
        assert (new_items, len(hits)) == (1, 1)
        assert hits[0]["tid"] == 1

    def test_old_post_skipped_but_marked_seen(self, tmp_path):
        s = self._store(tmp_path)
        old = _item(2, "出 旧帖被算法翻出",
                    create_time=str(int(time.time()) - 25 * 3600))   # 默认窗 24h
        assert s.ingest([old], None) == (1, [])       # 记为新条目,但不产生命中
        assert s.hit_count() == 0
        assert s.ingest([old], None) == (0, [])       # seen 已记:不再重复处理
        assert s.hit_count() == 0

    def test_missing_create_time_fails_open(self, tmp_path):
        """无 create_time(频道源条目)放行记录——宁可多提醒也不漏帖。"""
        s = self._store(tmp_path)
        new_items, hits = s.ingest([_item(3, "出 频道源帖")], None)
        assert (new_items, len(hits)) == (1, 1)

    def test_unparseable_create_time_fails_open(self, tmp_path):
        s = self._store(tmp_path)
        assert len(s.ingest([_item(4, "出 怪字段帖", create_time="刚刚")], None)[1]) == 1

    def test_setting_48h_lets_25h_old_post_through(self, tmp_path):
        s = self._store(tmp_path)
        old = _item(5, "出 25 小时前的帖", create_time=str(int(time.time()) - 25 * 3600))
        assert s.ingest([old], None) == (1, [])       # 24h 窗内被跳过
        s.set_settings(48)
        assert len(s.ingest([old], None)[1]) == 0     # 已进 seen,不会再处理
        # 换一条新的 25h 旧帖验证 48h 窗放行
        assert len(s.ingest([_item(6, "出 另一条 25 小时前的帖",
                                  create_time=str(int(time.time()) - 25 * 3600))], None)[1]) == 1

    def test_settings_default_validation_and_persist(self, tmp_path):
        s = WatchStore(str(tmp_path))
        assert s.get_settings() == {"window_hours": 24}
        assert s.status()["window_hours"] == 24
        with pytest.raises(ValueError):
            s.set_settings(6)
        assert s.get_settings()["window_hours"] == 24          # 非法值不改动
        assert s.set_settings(12) == {"window_hours": 12}
        assert WatchStore(str(tmp_path)).get_settings()["window_hours"] == 12   # 持久化重载

    def test_corrupt_settings_fall_back_to_default(self, tmp_path):
        (tmp_path / "sdgun_watch.json").write_text(
            json.dumps({"rules": [], "hits": [], "settings": {"window_hours": 999}}),
            encoding="utf-8")
        assert WatchStore(str(tmp_path)).get_settings()["window_hours"] == 24


    def test_hit_records_publish_time(self, tmp_path):
        s = self._store(tmp_path)
        now = int(time.time())
        _, hits = s.ingest([_item(1, "出 有发布时间的帖", create_time=str(now - 600)),
                            _item(2, "出 频道源帖")], None)
        assert [h["publish_time"] for h in hits] == [now - 600, 0]   # 无 create_time 记 0


class TestRuleCreationGate:
    """规则只对"创建之后发布"的帖生效:threadList 按最后活动置顶排序,旧帖被回帖
    翻进列表时不该提醒(与基线不回溯一致)。"""

    def _store(self, tmp_path, now, rule_age_sec=3600):
        s = WatchStore(str(tmp_path))
        rule = s.add_rule("", "出")
        s._rules[0]["created_at"] = now - rule_age_sec
        s.ingest([_item(100, "基线")], None)
        return s, rule["id"]

    def test_post_older_than_rule_is_not_a_hit(self, tmp_path):
        now = int(time.time())
        s, _rid = self._store(tmp_path, now, rule_age_sec=3600)
        # 20 小时前发的帖(在 24h 时间窗内,但早于规则创建)→ 只记 seen,不提醒
        old = _item(1, "出 昨天发的帖被回帖翻上来", create_time=str(now - 20 * 3600))
        assert s.ingest([old], None) == (1, [])
        assert s.hit_count() == 0
        assert s.ingest([old], None) == (0, [])      # seen 已记
        assert s.hit_count() == 0

    def test_post_newer_than_rule_is_a_hit(self, tmp_path):
        now = int(time.time())
        s, _rid = self._store(tmp_path, now, rule_age_sec=3600)
        new_items, hits = s.ingest([_item(1, "出 刚发的帖", create_time=str(now - 60))], None)
        assert (new_items, len(hits)) == (1, 1)
        assert hits[0]["publish_time"] == now - 60

    def test_missing_create_time_passes_rule_gate(self, tmp_path):
        """无 create_time(频道源条目)无法判定新旧 → 放行,不因门槛漏帖。"""
        now = int(time.time())
        s, _rid = self._store(tmp_path, now, rule_age_sec=3600)
        assert len(s.ingest([_item(1, "出 频道源帖")], None)[1]) == 1

    def test_partial_rule_match_keeps_only_eligible_rules(self, tmp_path):
        now = int(time.time())
        s = WatchStore(str(tmp_path))
        old_rule = s.add_rule("", "出")
        new_rule = s.add_rule("", "glock")
        s._rules[0]["created_at"] = now - 48 * 3600   # 两天前建的规则
        s._rules[1]["created_at"] = now - 60          # 一分钟前刚建
        s.ingest([_item(100, "基线")], None)

        # 帖发布于 2 小时前:命中两条规则的关键字,但只该算老规则的
        _, hits = s.ingest([_item(1, "出 Glock 17", create_time=str(now - 2 * 3600))], None)
        assert len(hits) == 1
        assert hits[0]["rule_ids"] == [old_rule["id"]]
        assert hits[0]["keywords"] == ["出"]
        assert new_rule["id"] not in hits[0]["rule_ids"]

    def test_all_rules_ineligible_produces_no_hit(self, tmp_path):
        now = int(time.time())
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s.add_rule("", "glock")
        s._rules[0]["created_at"] = now - 60
        s._rules[1]["created_at"] = now - 60
        s.ingest([_item(100, "基线")], None)
        assert s.ingest([_item(1, "出 Glock", create_time=str(now - 7200))], None) == (1, [])
        assert s.hit_count() == 0

    def test_gate_stacks_with_window_hours(self, tmp_path):
        """时间窗与规则门槛叠加:晚于规则创建但超出时间窗的帖仍不提醒。"""
        now = int(time.time())
        s = WatchStore(str(tmp_path))
        s.add_rule("", "出")
        s._rules[0]["created_at"] = now - 30 * 3600   # 规则比时间窗更早
        s.ingest([_item(100, "基线")], None)
        # 25 小时前发布:晚于规则创建,但超出 24h 时间窗 → 不命中
        assert s.ingest([_item(1, "出 25 小时前", create_time=str(now - 25 * 3600))], None) == (1, [])
        s.set_settings(48)                             # 放宽时间窗后仍受规则门槛约束
        assert len(s.ingest([_item(2, "出 26 小时前", create_time=str(now - 26 * 3600))], None)[1]) == 1


class TestWatchRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        import sdgun_proxy
        return TestClient(sdgun_proxy.app)

    @pytest.fixture()
    def store(self, monkeypatch, tmp_path):
        st = WatchStore(str(tmp_path))
        monkeypatch.setattr(sdgun_watch, "store", st)
        return st

    def test_rules_add_toggle_remove_roundtrip(self, client, store):
        resp = client.post("/watch/rules", json={"keywords": "出, glock", "name": "交易"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rule"]["keywords"] == ["出", "glock"]
        assert [r["keywords"] for r in body["rules"]] == [["出", "glock"]]
        assert client.get("/watch/rules").json()["rules"][0]["name"] == "交易"

        rid = body["rule"]["id"]
        toggled = client.post("/watch/rules/toggle", json={"id": rid}).json()
        assert toggled["rule"]["enabled"] is False
        assert toggled["rules"][0]["enabled"] is False

        removed = client.post("/watch/rules/remove", json={"id": rid}).json()
        assert removed["removed"] is True
        assert removed["rules"] == []

    def test_invalid_keywords_returns_400(self, client, store):
        resp = client.post("/watch/rules", json={"keywords": "   "})
        assert resp.status_code == 400
        assert resp.json()["msg"]

    def test_hits_cursor(self, client, store):
        store.add_rule("", "出")
        store.ingest([_item(100, "基线")], None)
        store.ingest([_item(101, "出 Glock"), _item(102, "出 AK")], None)

        body = client.get("/watch/hits", params={"since": -1}).json()
        assert [h["hit_id"] for h in body["hits"]] == [1, 2]
        assert body["max_hit_id"] == 2
        assert body["total"] == 2

        first = body["hits"][0]["hit_id"]
        newer = client.get("/watch/hits", params={"since": first}).json()
        assert [h["hit_id"] for h in newer["hits"]] == [2]

    def test_status(self, client, store):
        store.add_rule("", "出")
        st = client.get("/watch/status").json()
        assert st["rule_count"] == 1
        assert st["hit_count"] == 0
        assert st["interval"] >= 20
        assert st["source"] is None
        assert st["last_error"] is None
        assert st["window_hours"] == 24

    def test_settings_get_set_and_persist(self, client, store, tmp_path):
        assert client.get("/watch/settings").json() == {"window_hours": 24}
        r = client.post("/watch/settings", json={"window_hours": 48})
        assert r.status_code == 200
        assert r.json() == {"window_hours": 48}
        assert client.get("/watch/settings").json()["window_hours"] == 48
        assert client.get("/watch/status").json()["window_hours"] == 48
        assert WatchStore(str(tmp_path)).get_settings()["window_hours"] == 48

    def test_settings_rejects_invalid_value(self, client, store):
        r = client.post("/watch/settings", json={"window_hours": 6})
        assert r.status_code == 400
        assert "12/24/48" in r.json()["msg"]
        assert client.get("/watch/settings").json()["window_hours"] == 24   # 未改动

    def test_run_passes_through_poll_result(self, client, store, monkeypatch):
        fake = {"ok": True, "new_items": 3, "hits_added": 1, "error": None}
        monkeypatch.setattr(sdgun_watch, "poll_once", lambda _st: fake)
        assert client.post("/watch/run").json() == fake
