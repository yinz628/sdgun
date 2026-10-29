import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Avatar, Button, Card, Descriptions, Empty, Image, Input, List, Modal, Pagination,
  Popconfirm, Space, Spin, Tabs, Tag, Typography, message,
} from 'antd';
import { ApiError } from '../api/client';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage } from '../utils/media';
import { parseChatMessage } from '../utils/chat';
import { fmtEpochSec } from '../utils/notify';
import { activityToFeed } from '../utils/adapters';
import { toastErr } from '../utils/toast';
import type {
  ChatMsgItem, FavoriteRecord, InfoFeedItem, LikeRecord, LoadUserItem, MyFollowItem, UserActivityItem, UserDetailResponse, UserHomeResponse,
} from '../types/sdgun-api-types';

const T_STEP = 20;

interface Props {
  userId: number | string;
  /** 已知昵称（搜索结果/楼主栏带来，避免等 loadUsers） */
  nameHint?: string;
  /** 当前登录用户（jsApi），私信换取 chatToken / 发送者信息用 */
  me?: { user_id: number; name: string; head: string } | null;
  /** 关闭用户主页，返回帖子详情 */
  onClose: () => void;
  /** 进入主页即展开私信面板（从会话列表/新消息提醒点进来时用） */
  autoOpenChat?: boolean;
  /** 点击其他用户（关注列表项等）→ 打开对方主页 */
  onOpenUser?: (userId: number | string, name?: string) => void;
  /** 点击该用户的帖子 → 右侧切换为帖子详情 */
  onOpenThread: (item: InfoFeedItem) => void;
}

/** 右侧：用户主页（头部资料 + 动态/论坛帖子列表 + 私信聊天） */
export default function UserPanel({ userId, nameHint, me, onClose, onOpenThread, autoOpenChat = false, onOpenUser }: Props) {
  const [home, setHome] = useState<UserHomeResponse | null>(null);
  const [profile, setProfile] = useState<LoadUserItem | null>(null);
  const [detail, setDetail] = useState<UserDetailResponse | null>(null);
  const [homeLoading, setHomeLoading] = useState(true);

  const [tab, setTab] = useState<'detail' | 'activity' | 'forum' | 'follows' | 'likes' | 'favs'>('activity');
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
  const isMe = me != null && Number(userId) === me.user_id;

  const [followed, setFollowed] = useState(false);
  const [following, setFollowing] = useState(false);

  // 私信聊天面板
  const [chatOpen, setChatOpen] = useState(false);
  const [chatToken, setChatToken] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatMsgs, setChatMsgs] = useState<ChatMsgItem[]>([]);
  const [chatText, setChatText] = useState('');
  const [sending, setSending] = useState(false);
  const seenIds = useRef<Set<number>>(new Set());

  const peerId = `user${userId}`;
  const myId = me ? `user${me.user_id}` : '';

  /** 合并消息（按 msg_id 去重、时间排序），只保留与会话双方相关的 */
  const mergeMsgs = useCallback(
    (incoming: ChatMsgItem[]) => {
      const related = incoming.filter(
        (m) => m.from_user_id === peerId || m.to_user_id === peerId,
      );
      setChatMsgs((prev) => {
        const map = new Map<number, ChatMsgItem>();
        for (const m of [...prev, ...related]) {
          if (!seenIds.current.has(m.msg_id)) {
            seenIds.current.add(m.msg_id);
            map.set(m.msg_id, m);
          } else {
            map.set(m.msg_id, m);
          }
        }
        return [...map.values()].sort((a, b) => a.create_time - b.create_time);
      });
    },
    [peerId],
  );

  const pollUnread = useCallback(
    async (token: string) => {
      try {
        const ur = await sdgunApi.chatUnread(token, me!.user_id);
        if (ur.msgs?.length) mergeMsgs(ur.msgs);
      } catch {
        /* 轮询失败静默，下次再试 */
      }
    },
    [me, mergeMsgs],
  );

  const openChatRef = useRef<() => Promise<void>>();

  /** 打开聊天:本地归档历史 → 换 chatToken → 补拉一次未读。
   *  后续新消息由 App 层的全局监听器(15s 轮询)经 sdgun-incoming-msgs 事件推送,
   *  本面板不再自建 interval(避免泄漏与双重消费)。 */
  const openChat = async () => {
    setChatOpen(true);
    if (!me) {
      message.warning('未登录，无法使用私信');
      return;
    }
    setChatLoading(true);
    try {
      try {
        const hist = await sdgunApi.localMessages(`user${userId}`);
        mergeMsgs(hist.msgs);
      } catch { /* 归档缺失不阻塞聊天 */ }
      const rt = await sdgunApi.repairChatToken(me.user_id);
      const token = rt.data?.chatToken ?? '';
      setChatToken(token);
      if (token) await pollUnread(token);
    } catch (e) {
      toastErr('聊天凭证获取失败', e);
    } finally {
      setChatLoading(false);
    }
  };

  // openChat 每次渲染都会重建,用 ref 保存最新引用(供挂载时的自动展开调用,避免依赖抖动);
  // 本 effect 声明在自动展开 effect 之前,保证首次挂载时 ref 已就绪
  useEffect(() => {
    openChatRef.current = openChat;
  });

  // 从会话列表/新消息提醒进入时自动展开私信面板
  useEffect(() => {
    if (autoOpenChat) void openChatRef.current?.();
  }, [autoOpenChat, userId]);

  const closeChat = () => {
    setChatOpen(false);
  };

  // 订阅全局监听器的新消息事件(chatOpen 关闭时忽略,消息已在代理侧归档)
  useEffect(() => {
    const onIncoming = (e: Event) => {
      const msgs = (e as CustomEvent<ChatMsgItem[]>).detail ?? [];
      if (chatOpen && msgs.length) mergeMsgs(msgs);
    };
    window.addEventListener('sdgun-incoming-msgs', onIncoming);
    return () => window.removeEventListener('sdgun-incoming-msgs', onIncoming);
  }, [chatOpen, mergeMsgs]);

  /** 发送私信（sign 固定假值；上游偶发慢响应，超时不代表失败 → 刷新未读确认） */
  const doSend = async () => {
    const text = chatText.trim();
    if (!text || !me || !chatToken) return;
    setSending(true);
    try {
      const resp = await sdgunApi.sendChatMsg({
        chatToken,
        meId: me.user_id,
        toUserId: userId,
        text,
        myName: me.name,
        myHead: me.head,
      });
      if (resp.data) mergeMsgs([resp.data]);
      setChatText('');
    } catch (e) {
      message.error(`发送失败：${(e as ApiError).message}（5 秒后自动查收确认）`);
      setTimeout(() => void pollUnread(chatToken), 5000);
    } finally {
      setSending(false);
    }
  };

  const displayName = profile?.name ?? nameHint ?? (home?.data?.sharedata?.title?.replace?.(/的.*$/, '') ?? `用户${userId}`);

  // 1) 主页框架 + 明文资料（userHome 的 name 是 RSA 密文，明文昵称取 loadUsers）
  useEffect(() => {
    setHomeLoading(true);
    setHome(null);
    setProfile(null);
    setDetail(null);
    setAllItems([]);
    setListPage(1);
    setFollowed(false);
    // 组件按位置复用,切换用户必须重置聊天面板(残留消息/会话态会串到下一用户)
    seenIds.current.clear();
    setChatOpen(false);
    setChatToken('');
    setChatMsgs([]);
    setChatText('');
    setFollows([]);
    setFollowsPage(1);
    setFollowsDone(false);
    setLikes([]);
    setLikesLoaded(false);
    setFavs([]);
    setFavsLoaded(false);
    (async () => {
      try {
        const h = await sdgunApi.userHome(userId);
        setHome(h);
      } catch (e) {
        toastErr('用户主页加载失败', e);
      } finally {
        setHomeLoading(false);
      }
      try {
        const lu = await sdgunApi.loadUsers([userId]);
        setProfile(lu.list?.[0] ?? null);
      } catch {
        /* 昵称缺失不阻塞 */
      }
    })();
    // 卸载时清空去重集合;与上面的「切换用户重置」合并到同一 effect,避免两段清理逻辑漂移
    return () => {
      seenIds.current.clear();
    };
  }, [userId]);

  // 2) 列表 tab：动态 / 论坛（点击条目 → 帖子详情）
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

  const doFollow = async () => {
    setFollowing(true);
    try {
      const resp = await sdgunApi.followUser(userId);
      setFollowed(true);
      message.success(resp.code === 100 ? `已关注 ${displayName}` : '该用户之前已关注');
    } catch (e) {
      toastErr('关注失败', e);
    } finally {
      setFollowing(false);
    }
  };

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
    <Card
      title={`用户主页 · ${displayName}`}
      size="small"
      extra={
        <Button size="small" onClick={onClose}>
          返回帖子
        </Button>
      }
    >
      <Spin spinning={homeLoading}>
        {/* 头部：头像 + 昵称/签名/用户组 + 统计 + 操作 */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', marginBottom: 8 }}>
          <Avatar size={64} src={proxiedImage(home?.data?.head || profile?.head)}>
            {displayName?.[0]}
          </Avatar>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Space size={8} wrap>
              <Typography.Text strong style={{ fontSize: 16 }}>{displayName}</Typography.Text>
              {home?.data?.group_name && <Tag color="geekblue">{home.data.group_name}</Tag>}
              {home?.data?.is_vip === 1 && <Tag color="gold">VIP</Tag>}
              {home?.data?.isChat && <Tag color="green">可私信</Tag>}
            </Space>
            <div>
              <Typography.Text type="secondary">
                {home?.data?.sign || profile?.sign || '这人很懒哟，没有签名！'}
              </Typography.Text>
            </div>
            <Space size={16} style={{ marginTop: 6 }} wrap>
              <Typography.Text type="secondary">粉丝 {home?.data?.fans_count ?? '-'}</Typography.Text>
              <Typography.Text type="secondary">关注 {home?.data?.follow_count ?? '-'}</Typography.Text>
              <Typography.Text type="secondary">访问 {home?.data?.click ?? '-'}</Typography.Text>
              <Typography.Text type="secondary">
                最后登录 {home?.data?.last_login_time ? new Date(home.data.last_login_time * 1000).toLocaleString() : '-'}
              </Typography.Text>
            </Space>
          </div>
          <Space direction="vertical">
            <Popconfirm title={`关注 ${displayName}？`} onConfirm={() => void doFollow()}>
              <Button size="small" type="primary" ghost loading={following} disabled={followed}>
                {followed ? '已关注' : '关注'}
              </Button>
            </Popconfirm>
            <Button size="small" onClick={() => void openChat()} disabled={!home?.data?.isChat}>
              私信
            </Button>
          </Space>
        </div>

        <Tabs
          activeKey={tab}
          onChange={(k) => { setTab(k as typeof tab); setListPage(1); }}
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
      </Spin>

      <Modal
        open={chatOpen}
        title={`私信 · ${displayName}`}
        onCancel={closeChat}
        width={520}
        footer={null}
      >
        <Spin spinning={chatLoading}>
          {/* 消息流：自己的靠右（蓝底），对方的靠左；content 是 {"text": "..."} JSON */}
          <div
            style={{
              height: 320,
              overflowY: 'auto',
              background: '#f5f5f5',
              borderRadius: 8,
              padding: 12,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            {chatMsgs.length === 0 && !chatLoading ? (
              <Typography.Text type="secondary" style={{ textAlign: 'center', marginTop: 120 }}>
                暂无消息，发送第一条私信吧
              </Typography.Text>
            ) : (
              chatMsgs.map((m) => {
                const mine = m.from_user_id === myId;
                // content 自适应解析:文本消息取正文,图片消息渲染缩略图(走代理)
                const { text, images } = parseChatMessage(m.content);
                return (
                  <div
                    key={m.msg_id}
                    style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start' }}
                  >
                    <div
                      style={{
                        maxWidth: '72%',
                        padding: '6px 10px',
                        borderRadius: 8,
                        background: mine ? '#1677ff' : '#fff',
                        color: mine ? '#fff' : 'rgba(0,0,0,0.88)',
                        fontSize: 13,
                        lineHeight: 1.6,
                        wordBreak: 'break-word',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {text}
                      {images.map((u) => (
                        <Image
                          key={u}
                          src={proxiedImage(u)}
                          fallback={u}
                          width={200}
                          style={{ borderRadius: 6, marginTop: text ? 4 : 0, display: 'block' }}
                        />
                      ))}
                      {!text && images.length === 0 && '（暂不支持的消息类型）'}
                    </div>
                  </div>
                );
              })
            )}
          </div>
          <Space.Compact style={{ width: '100%', marginTop: 10 }}>
            <Input
              placeholder={chatToken ? `发消息给 ${displayName}…` : '正在获取聊天凭证…'}
              value={chatText}
              disabled={!chatToken || sending}
              onChange={(e) => setChatText(e.target.value)}
              onPressEnter={() => void doSend()}
              maxLength={500}
            />
            <Button type="primary" loading={sending} disabled={!chatToken || !chatText.trim()} onClick={() => void doSend()}>
              发送
            </Button>
          </Space.Compact>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
            历史来自代理本地归档（/messages/local，自部署起累积）；新消息由全局监听每 15 秒
            轮询推送。上游未读拉取即清、无历史接口（App 走环信长连接），App 先读走的消息无法归档。
          </Typography.Paragraph>
        </Spin>
      </Modal>
    </Card>
  );
}
