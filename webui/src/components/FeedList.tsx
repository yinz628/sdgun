import { useEffect, useState } from 'react';
import {
  Avatar, Button, Card, Empty, Input, List, Pagination, Segmented, Select,
  Space, Tag, Typography, message,
} from 'antd';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage } from '../utils/media';
import { sanitizeHtml } from '../utils/sanitize';
import { searchItemToFeed, threadListItemToFeed } from '../utils/adapters';
import { toastErr } from '../utils/toast';
import type {
  ChannelCatItem,
  IndexFragmentItem,
  InfoFeedItem,
  SearchThreadItem,
  UserSearchItem,
} from '../types/sdgun-api-types';

// 每页条数(上游实测支持 step=100:分类流/频道流/搜索均如实返回);
// 20 为默认,可经分页器切换 20/50/100
const STEP_OPTIONS = [20, 50, 100];

/** 栏目定义：column_type=7 频道 / column_type=1 信息流分类 / 版块（circleTree→fid） */
interface TabDef {
  key: string;
  kind: 'channel' | 'cat' | 'forum';
  id: number;
  name: string;
  group?: string; // 版块所属分组名
}

interface Props {
  selectedId?: number;
  onSelect: (item: InfoFeedItem) => void;
  /** 用户搜索结果点击头像/昵称 → 打开用户主页 */
  onOpenUser: (userId: number, name?: string) => void;
}

/** 左侧：搜索（帖子/用户）+ 栏目下拉 + 信息流列表 + 分页 */
export default function FeedList({ selectedId, onSelect, onOpenUser }: Props) {
  const [tabs, setTabs] = useState<TabDef[]>([]);
  const [tabsLoading, setTabsLoading] = useState(true);
  const [activeKey, setActiveKey] = useState<string>();

  const [cats, setCats] = useState<ChannelCatItem[]>([]);
  const [catId, setCatId] = useState<number>();
  const [catLabel, setCatLabel] = useState<string>('');

  const [keyword, setKeyword] = useState(''); // 非空 = 搜索模式
  const [searchType, setSearchType] = useState<'thread' | 'user'>('thread');
  const [page, setPage] = useState(1);
  const [stepSize, setStepSize] = useState(20);   // 每页条数,可切 20/50/100
  const [items, setItems] = useState<InfoFeedItem[]>([]);
  const [rawSearch, setRawSearch] = useState<SearchThreadItem[]>([]);
  const [userResults, setUserResults] = useState<UserSearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  /** 频道流 p=1 返回的 totalList：全量有序 id，作为翻页游标（content_id 参数） */
  const [totalIds, setTotalIds] = useState<number[]>([]);

  const [hotWords, setHotWords] = useState<string[]>([]);
  const [followedIds, setFollowedIds] = useState<Set<number>>(new Set());
  const [followingId, setFollowingId] = useState<number | null>(null);

  const active = tabs.find((t) => t.key === activeKey);
  const searching = keyword.length > 0;

  // 1) 启动：拉首页框架解析栏目 + 版块树 + 热门搜索词
  //    栏目是整页的骨架,失败自动重试一次(上游 CDN 偶发瞬时抖动),再失败才报错。
  //    这里带 { retry: false } 关掉 client 内建重试:本组件这层 3s 递归重试就是
  //    唯一重试层,否则两层叠加最坏发 4 次请求(韧性不变,叠加消除)。
  useEffect(() => {
    (async () => {
      setTabsLoading(true);
      const defs: TabDef[] = [];
      const loadIndexPages = async (retried: boolean): Promise<void> => {
        try {
          const resp = await sdgunApi.indexPages({ retry: false });
          const frag = JSON.parse(resp.index_fragment) as IndexFragmentItem[];
          for (const f of frag) {
            if (f.column_type === 7)
              defs.push({ key: `ch-${f.id}`, kind: 'channel', id: Number(f.id), name: f.name });
            else if (f.column_type === 1)
              defs.push({ key: `cat-${f.id}`, kind: 'cat', id: Number(f.id), name: f.name });
          }
        } catch (e) {
          if (!retried) {
            await new Promise((r) => setTimeout(r, 3000));
            return loadIndexPages(true);
          }
          toastErr('栏目加载失败', e);
        }
      };
      await loadIndexPages(false);
      try {
        // 版块树（分组 → 版块 fid）；频道信息流服务端不分页，版块浏览走 threadList（真实分页）
        const tree = await sdgunApi.circleTree();
        for (const g of tree.list ?? []) {
          for (const c of g.circle_list ?? []) {
            const fid = Number(c.type_value);
            if (Number.isFinite(fid) && fid > 0)
              defs.push({ key: `fid-${fid}`, kind: 'forum', id: fid, name: c.name, group: g.name });
          }
        }
      } catch {
        /* 版块树失败不阻塞频道栏目 */
      }
      setTabs(defs);
      setActiveKey(defs[0]?.key);
      setTabsLoading(false);
      try {
        const tools = await sdgunApi.defaultTools();
        setHotWords((tools.hot_search_word_list ?? []).map((w) => w.name));
      } catch {
        /* 热搜词失败不阻塞主流程 */
      }
    })();
  }, []);

  // 2) 切栏目：频道类需要再拉该频道的分类列表（搜索模式下跳过）；重置分页游标
  useEffect(() => {
    if (searching) return;
    setPage(1);
    setCats([]);
    setCatId(undefined);
    setCatLabel('');
    setTotalIds([]);
    if (!active || active.kind !== 'channel') return;
    (async () => {
      try {
        const cfg = await sdgunApi.channelConfig(active.id);
        const list = cfg.data.info_config ?? [];
        setCats(list);
        setCatId(list[0]?.cat_id ?? 2);
      } catch (e) {
        toastErr('频道分类加载失败', e);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, searching]);

  // 3) 加载列表：搜索(帖子/用户) / 版块(threadList) / 栏目信息流
  useEffect(() => {
    if (searching) {
      (async () => {
        setLoading(true);
        try {
          if (searchType === 'user') {
            const resp = await sdgunApi.searchUsers(keyword);
            setUserResults(resp.list ?? []);
            setItems([]);
            setRawSearch([]);
          } else {
            const resp = await sdgunApi.searchThreads(keyword, page, stepSize);
            const raw = resp.list ?? [];
            setRawSearch(raw);
            setItems(raw.map(searchItemToFeed));
            setUserResults([]);
            if (page > 1 && raw.length === 0) message.info('没有更多结果了');
          }
        } catch (e) {
          setItems([]);
          setUserResults([]);
          toastErr('搜索失败', e);
        } finally {
          setLoading(false);
        }
      })();
      return;
    }
    if (!active) return;
    if (active.kind === 'forum') {
      (async () => {
        setLoading(true);
        try {
          const resp = await sdgunApi.forumThreads(active.id, page, stepSize);
          const list = resp.list ?? [];
          setRawSearch([]);
          setUserResults([]);
          setItems(list.map(threadListItemToFeed));
          if (page > 1 && list.length === 0) message.info('没有更多内容了');
        } catch (e) {
          setItems([]);
          toastErr('版块加载失败', e);
        } finally {
          setLoading(false);
        }
      })();
      return;
    }
    if (active.kind === 'channel' && !catId) return;
    (async () => {
      setLoading(true);
      try {
        let resp: Awaited<ReturnType<typeof sdgunApi.feed>>;
        if (active.kind === 'channel') {
          // 频道流真实分页：p>=2 必须带 content_id = totalList 的对应切片，否则服务端返回空
          const cursor = page > 1 ? totalIds.slice((page - 1) * stepSize, page * stepSize) : undefined;
          resp = await sdgunApi.feed({
            channel_id: active.id,
            cat_id: catId!,
            p: page,
            step: stepSize,
            contentIds: cursor,
          });
          if (page === 1) setTotalIds(resp.totalList ?? []);
        } else {
          resp = await sdgunApi.infoListV3(active.id, page, stepSize);
        }
        setRawSearch([]);
        setUserResults([]);
        setItems(resp.list ?? []);
        if (resp.cat_name) setCatLabel(resp.cat_name);
        if (page > 1 && (resp.list ?? []).length === 0) message.info('没有更多内容了');
      } catch (e) {
        setItems([]);
        toastErr('信息流加载失败', e);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, catId, page, keyword, searchType, stepSize]);

  /** 关注用户（写操作） */
  const doFollow = async (u: UserSearchItem) => {
    setFollowingId(u.user_id);
    try {
      const resp = await sdgunApi.followUser(u.user_id);
      message.success(resp.code === 100 ? `已关注 ${u.name}` : `${u.name} 之前已关注`);
      setFollowedIds((prev) => new Set(prev).add(u.user_id));
    } catch (e) {
      toastErr('关注失败', e);
    } finally {
      setFollowingId(null);
    }
  };

  // 服务端多数不返回 total:此前按"当前页+1"窗口估算,simple 分页器输入大页码
  // (如 100)会被组件收敛到窗口内,表现为"跳页不生效"。改为开放 PAGE_WINDOW
  // 页的跳页窗口,翻到空页再收缩为当前页(即末页);频道流例外——p=1 的
  // totalList 即全量 id,可精确分页(+少量广告位)
  const PAGE_WINDOW = 100;
  const estimatedTotal =
    !searching && active?.kind === 'channel' && totalIds.length > 0
      ? totalIds.length + stepSize
      : items.length === 0 && page > 1
        ? page * stepSize
        : PAGE_WINDOW * stepSize;

  const title = searching
    ? `搜索“${keyword}”`
    : active
      ? active.kind === 'forum'
        ? `版块 · ${active.name}`
        : `信息流 · ${active.name}${catLabel ? ` / ${catLabel}` : ''}`
      : '信息流';

  return (
    <Card
      title={title}
      size="small"
      extra={
        <Space size={6}>
          {searching ? (
            <Button size="small" onClick={() => { setKeyword(''); setPage(1); }}>
              退出搜索
            </Button>
          ) : (
            <>
              <Select
                size="small"
                style={{ minWidth: 110 }}
                loading={tabsLoading}
                value={activeKey}
                placeholder="栏目"
                onChange={(k) => setActiveKey(k)}
                options={tabs.map((t) => ({
                  value: t.key,
                  label: (
                    <Space size={4}>
                      {t.kind === 'forum' ? (
                        <Tag style={{ marginRight: 0 }} color="orange">
                          {t.group ?? '版块'}
                        </Tag>
                      ) : (
                        <Tag style={{ marginRight: 0 }} color={t.kind === 'channel' ? 'blue' : 'green'}>
                          {t.kind === 'channel' ? '频道' : '分类'}
                        </Tag>
                      )}
                      {t.name}
                    </Space>
                  ),
                }))}
              />
              {active?.kind === 'channel' && (
                <Select
                  size="small"
                  style={{ minWidth: 110 }}
                  value={catId}
                  placeholder="分类"
                  disabled={cats.length === 0}
                  onChange={(v) => { setCatId(v); setPage(1); }}
                  options={cats.map((c) => ({ value: c.cat_id, label: c.cat_name }))}
                />
              )}
            </>
          )}
        </Space>
      }
    >
      <Input.Search
        placeholder="搜索帖子 / 用户"
        allowClear
        enterButton
        style={{ marginBottom: searching ? 8 : 12 }}
        onSearch={(v) => { setKeyword(v.trim()); setPage(1); }}
      />
      {!searching && hotWords.length > 0 && (
        <Space size={4} wrap style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>热搜：</Typography.Text>
          {hotWords.map((w) => (
            <Tag key={w} style={{ cursor: 'pointer' }} onClick={() => { setKeyword(w); setPage(1); }}>
              {w}
            </Tag>
          ))}
        </Space>
      )}
      {searching && (
        <Segmented
          size="small"
          style={{ marginBottom: 12 }}
          value={searchType}
          onChange={(v) => { setSearchType(v as 'thread' | 'user'); setPage(1); }}
          options={[
            { label: '帖子', value: 'thread' },
            { label: '用户', value: 'user' },
          ]}
        />
      )}
      {/* 服务端搜索结果的高亮样式：<span class="active">关键词</span> */}
      <style>{`.feed-item-title .active { color: #1677ff; font-weight: 600; }`}</style>

      {searching && searchType === 'user' ? (
        <List
          loading={loading}
          dataSource={userResults}
          locale={{ emptyText: <Empty description={loading ? '加载中…' : '无匹配用户'} /> }}
          renderItem={(u) => (
            <List.Item
              key={u.user_id}
              actions={[
                followedIds.has(u.user_id) ? (
                  <Button key="done" size="small" disabled>已关注</Button>
                ) : (
                  <Button
                    key="follow"
                    size="small"
                    type="primary"
                    ghost
                    loading={followingId === u.user_id}
                    onClick={() => void doFollow(u)}
                  >
                    关注
                  </Button>
                ),
              ]}
            >
              <List.Item.Meta
                avatar={
                  <Avatar
                    style={{ cursor: 'pointer' }}
                    src={proxiedImage(u.user_head)}
                    onClick={(e) => { e?.stopPropagation(); onOpenUser(u.user_id, u.name); }}
                  />
                }
                title={
                  <span
                    className="feed-item-title"
                    style={{ cursor: 'pointer' }}
                    onClick={() => onOpenUser(u.user_id, u.name)}
                    dangerouslySetInnerHTML={{ __html: sanitizeHtml(u.user_title || u.name) }}
                  />
                }
                description={
                  <Typography.Text type="secondary" ellipsis style={{ maxWidth: 300 }}>
                    {u.sign || '这个人很懒，什么都没留下'}
                  </Typography.Text>
                }
              />
            </List.Item>
          )}
        />
      ) : (
        <List
          loading={loading}
          dataSource={items}
          locale={{ emptyText: <Empty description={searching ? '无搜索结果' : loading ? '加载中…' : '暂无内容'} /> }}
          renderItem={(item, idx) => (
            <List.Item
              key={item.id}
              style={{
                cursor: 'pointer',
                padding: '10px 12px',
                borderRadius: 6,
                background: selectedId === item.id ? '#e6f4ff' : undefined,
              }}
              onClick={() => onSelect(item)}
            >
              <List.Item.Meta
                title={
                  <Space size={6} wrap className="feed-item-title">
                    {searching && rawSearch[idx] ? (
                      <span dangerouslySetInnerHTML={{ __html: sanitizeHtml(rawSearch[idx].title) }} />
                    ) : (
                      <span>{item.title}</span>
                    )}
                    {item.red_packet && item.red_packet.has_red_packet === 1 && (
                      <Tag color="red">红包</Tag>
                    )}
                  </Space>
                }
                description={
                  <Typography.Text type="secondary" ellipsis style={{ maxWidth: 380 }}>
                    {item.forum_name ? `${item.forum_name} · ` : ''}
                    {item.source} · {item.publish_time_str} · {item.click}
                  </Typography.Text>
                }
              />
            </List.Item>
          )}
        />
      )}
      {!(searching && searchType === 'user') && (
        <Pagination
          style={{ marginTop: 12, textAlign: 'right' }}
          size="small"
          responsive
          showLessItems
          showSizeChanger
          pageSizeOptions={STEP_OPTIONS.map(String)}
          current={page}
          pageSize={stepSize}
          total={estimatedTotal}
          onChange={(p) => setPage(p)}
          onShowSizeChange={(_, s) => { setStepSize(s); setPage(1); }}
        />
      )}
    </Card>
  );
}
