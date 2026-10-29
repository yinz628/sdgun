import { useCallback, useEffect, useState } from 'react';
import {
  Avatar, Button, Descriptions, Empty, Image, List, Pagination, Space, Spin, Tabs, Tag,
  Typography, message,
} from 'antd';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage } from '../utils/media';
import { fmtEpochSec } from '../utils/notify';
import { activityToFeed } from '../utils/adapters';
import { toastErr } from '../utils/toast';
import type {
  FavoriteRecord, InfoFeedItem, LikeRecord, LoadUserItem, MyFollowItem, UserActivityItem, UserDetailResponse, UserHomeResponse,
} from '../types/sdgun-api-types';

const T_STEP = 20;

/** 内容 tab 的键（资料/动态/论坛 + 仅自己可见的关注/点赞/收藏） */
export type ProfileTabKey = 'detail' | 'activity' | 'forum' | 'follows' | 'likes' | 'favs';

interface Props {
  /** 当前 tab（由 UserPanel 持有切换 state） */
  tab: ProfileTabKey;
  onTabChange: (tab: ProfileTabKey) => void;
  userId: number | string;
  /** 是否本人主页（关注/点赞/收藏三个 tab 仅本人可见） */
  isMe: boolean;
  /** 当前登录用户（jsApi），关注列表按此拉取 */
  me?: { user_id: number; name: string; head: string } | null;
  /** 主页数据（关注 tab 标题里的 follow_count 用） */
  home?: UserHomeResponse | null;
  /** 明文资料（资料 tab 的生日/等级） */
  profile?: LoadUserItem | null;
  /** 点击其他用户（关注列表项等）→ 打开对方主页 */
  onOpenUser?: (userId: number | string, name?: string) => void;
  /** 点击该用户的帖子 → 右侧切换为帖子详情 */
  onOpenThread: (item: InfoFeedItem) => void;
}

/** 用户主页的内容 tab：动态 / 论坛帖子 / 资料（+ 本人的关注 / 点赞 / 收藏） */
export default function ProfileTabs({ tab, onTabChange, userId, isMe, me, home, profile, onOpenUser, onOpenThread }: Props) {
  const [detail, setDetail] = useState<UserDetailResponse | null>(null);
  const [allItems, setAllItems] = useState<UserActivityItem[]>([]);   // 全量(≤100 条),本地分页切片展示
  const [listLoading, setListLoading] = useState(false);
  const [listPage, setListPage] = useState(1);
  // 我的关注列表(仅自己的主页展示;关注后此前无处查看,见 2026-09-10 迭代)
  const [follows, setFollows] = useState<MyFollowItem[]>([]);
  const [followsPage, setFollowsPage] = useState(1);
  const [followsDone, setFollowsDone] = useState(false);
  const [followsLoading, setFollowsLoading] = useState(false);
  // 我的点赞(网页端本地归档 sdgun_likes;上游无此接口,只能从功能上线起累积)
  const [likes, setLikes] = useState<LikeRecord[]>([]);
  const [likesLoading, setLikesLoading] = useState(false);
  const [likesLoaded, setLikesLoaded] = useState(false);
  // 我的收藏(网页端本地标记 sdgun_favorites;上游收藏接口未逆向)
  const [favs, setFavs] = useState<FavoriteRecord[]>([]);
  const [favsLoading, setFavsLoading] = useState(false);
  const [favsLoaded, setFavsLoaded] = useState(false);

  // 切换用户必须重置本组内容状态(组件按位置复用)
  useEffect(() => {
    setDetail(null);
    setAllItems([]);
    setListPage(1);
    setFollows([]);
    setFollowsPage(1);
    setFollowsDone(false);
    setLikes([]);
    setLikesLoaded(false);
    setFavs([]);
    setFavsLoaded(false);
  }, [userId]);

  // 列表 tab：动态 / 论坛（点击条目 → 帖子详情）
  // 上游 userActivityPage/userForumOrShowPage 实测忽略 p 参数(各页返回相同内容,
  // 2026-10-20 VPS 实测),但 step 如实生效——改为一次拉 step=100 全量 + 本地分页
  const loadAllItems = useCallback(
    async (which: 'activity' | 'forum') => {
      setListLoading(true);
      try {
        const resp =
          which === 'activity'
            ? await sdgunApi.userActivity(userId, 1, 100)
            : await sdgunApi.userForumOrShow(userId, 3, 1, 100);
        setAllItems(resp.list ?? []);
      } catch (e) {
        setAllItems([]);
        toastErr('用户内容加载失败', e);
      } finally {
        setListLoading(false);
      }
    },
    [userId],
  );

  /** 我的关注列表(仅自己主页;MyFollowsList 无 total,条数<step 即末页) */
  const loadFollows = useCallback(
    async (p: number) => {
      if (!me) return;
      setFollowsLoading(true);
      try {
        const resp = await sdgunApi.myFollowsList(me.user_id, p);
        setFollows(resp.list ?? []);
        setFollowsPage(p);
        setFollowsDone((resp.list ?? []).length < 20);
      } catch (e) {
        setFollows([]);
        toastErr('关注列表加载失败', e);
      } finally {
        setFollowsLoading(false);
      }
    },
    [me],
  );

  useEffect(() => {
    if (isMe && tab === 'follows') void loadFollows(1);
  }, [isMe, tab, loadFollows]);

  // 点赞/收藏的加载:显式 loaded 标志做"只拉一次"守卫。空列表时不能用
  // length>0 当守卫(永不命中→请求完成后 effect 重跑→无限循环+loading 闪屏);
  // 失败不标 loaded——切走再切回 tab 即重试,但不会原地循环
  useEffect(() => {
    if (!isMe || tab !== 'likes' || likesLoaded) return;
    setLikesLoading(true);
    sdgunApi.likesList()
      .then((r) => { setLikes(r.likes ?? []); setLikesLoaded(true); })
      .catch((e) => toastErr('点赞历史加载失败', e))
      .finally(() => setLikesLoading(false));
  }, [isMe, tab, likesLoaded]);

  useEffect(() => {
    if (!isMe || tab !== 'favs' || favsLoaded) return;
    setFavsLoading(true);
    sdgunApi.favoritesList()
      .then((r) => { setFavs(r.favorites ?? []); setFavsLoaded(true); })
      .catch((e) => toastErr('收藏加载失败', e))
      .finally(() => setFavsLoading(false));
  }, [isMe, tab, favsLoaded]);

  useEffect(() => {
    if (tab === 'detail') {
      if (!detail) {
        sdgunApi
          .userDetail(userId)
          .then(setDetail)
          .catch((e) => toastErr('资料加载失败', e));
      }
      return;
    }
    if (tab === 'follows' || tab === 'likes' || tab === 'favs') return;   // 由专属 effect 加载
    void loadAllItems(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, userId]);

  const infoSection = detail?.data?.find?.((s) => s.type === 'info');
  const infoRows = Array.isArray(infoSection?.data) ? (infoSection!.data as Array<{ name: string; value: string }>) : [];
  const tagSection = detail?.data?.find?.((s) => s.type === 'tagList');
  const tagRows = Array.isArray(tagSection?.data) ? (tagSection!.data as Array<{ id: number; name: string }>) : [];

  const renderList = (label: string) => {
    // 本地分页切片(上游忽略 p,已一次拉全量 ≤100 条存 allItems)
    const pageItems = allItems.slice((listPage - 1) * T_STEP, listPage * T_STEP);
    return (
    <Spin spinning={listLoading}>
      {allItems.length === 0 && !listLoading ? (
        <Empty description={`暂无${label}`} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        pageItems.map((a) => (
          <List.Item
            key={`${a.tid}-${a.create_time ?? ''}`}
            style={{ cursor: 'pointer', padding: '10px 6px', borderRadius: 6 }}
            onClick={() => onOpenThread(activityToFeed(a))}
          >
            <List.Item.Meta
              avatar={
                (a.pics_arr?.length ?? 0) > 0 ? (
                  <Image
                    src={proxiedImage(a.pics_arr[0]?.url)}
                    width={72}
                    height={54}
                    style={{ objectFit: 'cover' }}
                    preview={false}
                  />
                ) : undefined
              }
              title={
                <Space size={6} wrap>
                  <span>{a.title ?? '(无标题)'}</span>
                  {a.forum_name && <Tag style={{ marginRight: 0 }}>{a.forum_name}</Tag>}
                </Space>
              }
              description={
                <Typography.Text type="secondary">
                  {a.create_time_str} · {a.click} · 👍 {a.applaud_count}
                  {a.comment_count != null ? ` · 💬 ${a.comment_count}` : ''}
                  {a.ip_address ? ` · IP ${a.ip_address}` : ''}
                </Typography.Text>
              }
            />
          </List.Item>
        ))
      )}
      {/* 本地全量分页:total 精确(上限 100 条/上游单次最大拉取) */}
      <Pagination
        style={{ marginTop: 12, textAlign: 'right' }}
        simple
        current={listPage}
        pageSize={T_STEP}
        total={allItems.length}
        onChange={setListPage}
      />
    </Spin>
    );
  }

  return (
    <Tabs
      activeKey={tab}
      onChange={(k) => { onTabChange(k as ProfileTabKey); setListPage(1); }}
      items={[
        { key: 'activity', label: '动态', children: renderList('动态') },
        { key: 'forum', label: '论坛帖子', children: renderList('论坛帖子') },
        {
          key: 'detail',
          label: '资料',
          children: (
            <Descriptions size="small" column={2} bordered style={{ background: '#fff' }}>
              {infoRows.map((r) => (
                <Descriptions.Item key={r.name} label={r.name}>{r.value}</Descriptions.Item>
              ))}
              {tagRows.length > 0 && (
                <Descriptions.Item label="兴趣标签" span={2}>
                  <Space size={4} wrap>
                    {tagRows.map((t) => (
                      <Tag key={t.id} color="blue" style={{ cursor: 'pointer' }}>{t.name}</Tag>
                    ))}
                  </Space>
                </Descriptions.Item>
              )}
              {profile?.birth && (
                <Descriptions.Item label="生日">{profile.birth}</Descriptions.Item>
              )}
              {profile?.group_level && (
                <Descriptions.Item label="等级">Lv{profile.group_level}</Descriptions.Item>
              )}
            </Descriptions>
          ),
        },
        ...(isMe ? [{
          key: 'follows' as const,
          label: `关注${home?.data ? ` ${home.data.follow_count}` : ''}`,
          children: (
            <Spin spinning={followsLoading}>
              {follows.length === 0 && !followsLoading ? (
                <Typography.Text type="secondary">还没有关注任何人（在帖子或用户主页点「关注」即可）</Typography.Text>
              ) : (
                <List
                  size="small"
                  dataSource={follows}
                  rowKey={(u) => String(u.user_id)}
                  renderItem={(u) => (
                    <List.Item
                      style={{ cursor: 'pointer', padding: '8px 4px' }}
                      onClick={() => onOpenUser?.(u.user_id, u.name)}
                    >
                      <List.Item.Meta
                        avatar={<Avatar src={proxiedImage(u.head)} />}
                        title={<Typography.Text strong>{u.name}</Typography.Text>}
                      />
                    </List.Item>
                  )}
                />
              )}
              {follows.length > 0 && (
                <Space style={{ marginTop: 8 }}>
                  <Button size="small" disabled={followsPage <= 1} onClick={() => void loadFollows(followsPage - 1)}>上一页</Button>
                  <Typography.Text type="secondary">第 {followsPage} 页</Typography.Text>
                  <Button size="small" disabled={followsDone} onClick={() => void loadFollows(followsPage + 1)}>下一页</Button>
                </Space>
              )}
            </Spin>
          ),
        }] : []),
        ...(isMe ? [{
          key: 'likes' as const,
          label: '点赞',
          children: (
            <Spin spinning={likesLoading}>
              {likes.length === 0 && !likesLoading ? (
                <Typography.Text type="secondary">
                  还没有点赞记录。网页端的点赞会自动归档到这里（此前在 App 里点的赞上游不提供查询，无法找回）。
                </Typography.Text>
              ) : (
                <List
                  size="small"
                  dataSource={likes}
                  rowKey={(l) => String(l.tid)}
                  renderItem={(l) => (
                    <List.Item
                      style={{ cursor: 'pointer', padding: '8px 4px' }}
                      onClick={() => onOpenThread({ id: l.tid, title: l.title, user_name: '' } as InfoFeedItem)}
                    >
                      <List.Item.Meta
                        title={<Typography.Text strong>{l.title}</Typography.Text>}
                        description={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {fmtEpochSec(l.liked_at)} 赞过
                        </Typography.Text>}
                      />
                    </List.Item>
                  )}
                />
              )}
            </Spin>
          ),
        }] : []),
        ...(isMe ? [{
          key: 'favs' as const,
          label: '收藏',
          children: (
            <Spin spinning={favsLoading}>
              {favs.length === 0 && !favsLoading ? (
                <Typography.Text type="secondary">
                  还没有收藏。打开帖子的「☆ 收藏」按钮即可加入这里（收藏是本地标记，仅记录网页端的操作）。
                </Typography.Text>
              ) : (
                <List
                  size="small"
                  dataSource={favs}
                  rowKey={(f) => String(f.tid)}
                  renderItem={(f) => (
                    <List.Item
                      style={{ cursor: 'pointer', padding: '8px 4px' }}
                      onClick={() => onOpenThread({ id: f.tid, title: f.title, user_name: '' } as InfoFeedItem)}
                    >
                      <List.Item.Meta
                        title={<Typography.Text strong>★ {f.title}</Typography.Text>}
                        description={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {fmtEpochSec(f.added_at)} 收藏
                        </Typography.Text>}
                      />
                      <Button
                        size="small"
                        danger
                        onClick={(e) => {
                          e.stopPropagation();
                          sdgunApi.favoritesRemove(f.tid)
                            .then(() => setFavs((cur) => cur.filter((x) => x.tid !== f.tid)))
                            .catch(() => message.error('取消收藏失败'));
                        }}
                      >
                        取消收藏
                      </Button>
                    </List.Item>
                  )}
                />
              )}
            </Spin>
          ),
        }] : []),
      ]}
    />
  );
}
