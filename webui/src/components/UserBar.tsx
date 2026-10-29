import { useCallback, useEffect, useState } from 'react';
import { Alert, Avatar, Badge, Button, Drawer, Form, Input, List, Modal, Popconfirm, Popover, Segmented, Select, Space, Spin, Switch, Tag, Typography, message } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { ApiError } from '../api/client';
import { proxiedImage } from '../utils/media';
import {
  NOTIFY_LABEL, fmtEpochSec, loadWatchReadTids, migrateWatchReadFromHitIds,
  tidFromNotifyLink,
} from '../utils/notify';
import { reportWatchRead } from '../utils/uistate';
import { toastErr } from '../utils/toast';
import type { ConversationSummary, JsApiUser, NotifyIndexItem, NotifyItem, WatchHit, WatchRule, WatchStatus } from '../types/sdgun-api-types';

/** 提醒时间窗选项(与后端 WINDOW_HOURS_CHOICES 一致) */
const WATCH_WINDOW_OPTIONS = [12, 24, 48].map((h) => ({ value: h, label: `${h} 小时` }));

interface Props {
  onUserChange: (u: JsApiUser | null) => void;
  /** 全局监听累计的未读私信数(打开会话列表清零) */
  unread?: number;
  onClearUnread?: () => void;
  /** 通知中心未读数(回复/@/赞/关注;打开通知面板清零) */
  notifyUnseen?: number;
  onNotifyRead?: () => void;
  /** 打开与某用户的私信对话(进用户主页并展开聊天面板) */
  onOpenConversation?: (uid: number | string, name?: string, openChat?: boolean) => void;
  /** 从通知明细跳转帖子 */
  onOpenNotifyThread?: (link: string, title: string) => void;
  /** 关注关键字监控的未读命中数(由已读集合算出;标记已读后刷新) */
  watchUnseen?: number;
  onWatchRefresh?: () => void;
  /** 从命中记录跳转帖子 */
  onOpenWatchThread?: (tid: number, title: string) => void;
}

/** 会话已读标记(localStorage: peer → 最后已读 create_time),供列表显示未读点 */
const readMarkerOf = (peer: string): number =>
  Number(localStorage.getItem(`sdgunMsgRead:${peer}`)) || 0;
const markConversationRead = (peer: string, t: number) =>
  localStorage.setItem(`sdgunMsgRead:${peer}`, String(t));

/** 顶栏：当前用户（jsApi）+ 私信会话/通知中心 + 账号密码登录 + 会话 Cookie 设置 */
export default function UserBar({ onUserChange, unread = 0, onClearUnread,
                                   notifyUnseen = 0, onNotifyRead,
                                   onOpenConversation, onOpenNotifyThread,
                                   watchUnseen = 0, onWatchRefresh, onOpenWatchThread }: Props) {
  const [user, setUser] = useState<JsApiUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cookieDraft, setCookieDraft] = useState(() => localStorage.getItem('sdgunCookies') ?? '');
  const [open, setOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginFailMsg, setLoginFailMsg] = useState<string | null>(null);
  const [loginForm] = Form.useForm<{ account: string; password: string }>();
  // 私信会话列表(抽屉)
  const [convOpen, setConvOpen] = useState(false);
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [convLoading, setConvLoading] = useState(false);
  // 通知中心(弹出面板)
  const [notifyOpen, setNotifyOpen] = useState(false);
  const [notifyItems, setNotifyItems] = useState<{ code: string; label: string; items: NotifyItem[] }[]>([]);
  const [notifyLoading, setNotifyLoading] = useState(false);
  // 关注关键字监控(抽屉:规则管理 + 最近命中 + 状态)
  const [watchOpen, setWatchOpen] = useState(false);
  const [watchRules, setWatchRules] = useState<WatchRule[]>([]);
  const [watchHits, setWatchHits] = useState<WatchHit[]>([]);
  const [watchStatus, setWatchStatus] = useState<WatchStatus | null>(null);
  const [watchLoading, setWatchLoading] = useState(false);
  const [watchBusyId, setWatchBusyId] = useState<number | null>(null);
  const [watchRunning, setWatchRunning] = useState(false);
  const [watchTab, setWatchTab] = useState<'unread' | 'read'>('unread');
  /** 已读**帖子** tid 集合(不是 hit_id:同一帖子会因去重/裁剪/重现换 hit_id) */
  const [watchReadTids, setWatchReadTids] = useState<number[]>([]);
  const [newKeywords, setNewKeywords] = useState('');
  const [newRuleName, setNewRuleName] = useState('');

  const loadUser = useCallback(
    async (allowFallback = true): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const resp = await sdgunApi.currentUser();
        setUser(resp._USER);
        onUserChange(resp._USER);
      } catch (e) {
        // 本地覆盖的 Cookie 失效 → 自动降级为代理会话再试一次
        if (allowFallback && localStorage.getItem('sdgunCookies')) {
          localStorage.removeItem('sdgunCookies');
          setCookieDraft('');
          message.warning('本地 Cookie 已失效，已自动切换为代理会话');
          return loadUser(false);
        }
        setUser(null);
        onUserChange(null);
        setError((e as ApiError).message);
      } finally {
        setLoading(false);
      }
    },
    [onUserChange],
  );

  useEffect(() => { void loadUser(); }, [loadUser]);

  const saveCookie = () => {
    const v = cookieDraft.trim();
    if (v) localStorage.setItem('sdgunCookies', v);
    else localStorage.removeItem('sdgunCookies');
    message.success(v ? 'Cookie 已保存，正在刷新用户信息' : '已清空本地 Cookie（改用代理会话）');
    setOpen(false);
    void loadUser();
  };

  const useProxySession = () => {
    localStorage.removeItem('sdgunCookies');
    setCookieDraft('');
    setOpen(false);
    message.info('已切换为代理会话');
    void loadUser();
  };

  /** 一键登录：让代理重放最新抓包里的登录报文（含 mag-ss-key），成功即获得新会话 */
  const replayLogin = async () => {
    setReplaying(true);
    try {
      const j = await sdgunApi.authReplayExact();
      if (j.session_updated) {
        message.success('登录成功，已获取新会话');
        setLoginOpen(false);
        setOpen(false);
        void loadUser();
      } else {
        message.error(`登录未成功：${j.upstream?.msg ?? JSON.stringify(j).slice(0, 120)}`);
      }
    } catch (e) {
      toastErr('请求失败', e);
    } finally {
      setReplaying(false);
    }
  };

  /** 账号密码登录：代理走 WAP 通道（/mag/user/v1/User/login，明文表单）换取新会话 */
  const accountLogin = async (values: { account: string; password: string }) => {
    setLoggingIn(true);
    setLoginFailMsg(null);
    try {
      // 网络级重试一次(仅连接层失败)与失败文案(含上游锁定计数)均由 authLogin 负责
      const j = await sdgunApi.authLogin(values.account, values.password);
      if (j.success) {
        message.success(`登录成功：${j.user?.name ?? ''}（uid ${j.user?.user_id ?? '?'}）`);
        loginForm.resetFields();
        setLoginOpen(false);
        void loadUser();
      } else {
        // 上游 msg 含失败锁定计数（如"当前已失败 1/3 次"），必须完整展示给用户
        setLoginFailMsg(j.msg ?? '登录失败');
      }
    } catch (e) {
      setLoginFailMsg(`请求失败：${(e as Error).message}（请稍后重试）`);
    } finally {
      setLoggingIn(false);
    }
  };

  /** 打开私信会话列表(本地归档按对端分组);未登录则提示 */
  const openConversations = () => {
    if (!user) {
      message.info('未登录,登录后才能查看私信会话');
      return;
    }
    onClearUnread?.();
    setConvOpen(true);
    setConvLoading(true);
    sdgunApi.localConversations(user.user_id)
      .then((r) => setConvs(r.conversations ?? []))
      .catch((e) => toastErr('会话列表加载失败', e))
      .finally(() => setConvLoading(false));
  };

  /** 点击某个会话:记已读、关抽屉、进对端主页(聊天面板在那里) */
  const openConversation = (c: ConversationSummary) => {
    markConversationRead(c.peer_id, c.last_time);
    setConvOpen(false);
    const uid = Number(c.peer_id.replace('user', ''));
    onOpenConversation?.(uid, c.name || undefined, true);
  };

  /** 打开通知中心:拉各分类明细(只拉有未读的,最多 4 类),同时清本地未读角标 */
  const openNotify = (next: boolean) => {
    setNotifyOpen(next);
    if (!next) return;
    onNotifyRead?.();
    setNotifyLoading(true);
    setNotifyItems([]);
    (async () => {
      try {
        const idx = await sdgunApi.notifyIndex();
        const codes = (idx.data?.notifyList ?? [])
          .filter((i) => i.count > 0 && i.code !== 'chat')
          .map((i) => i.code)
          .slice(0, 4);
        const useCodes = codes.length ? codes : ['comment'];
        const results = await Promise.all(
          useCodes.map(async (code) => ({
            code,
            label: NOTIFY_LABEL[code] ?? code,
            items: ((await sdgunApi.notifyList(code)).list ?? []).slice(0, 10),
          })),
        );
        setNotifyItems(results);
      } catch (e) {
        toastErr('通知加载失败', e);
      } finally {
        setNotifyLoading(false);
      }
    })();
  };

  /** 点击通知条目:跳对应帖子 */
  const clickNotifyItem = (it: NotifyItem) => {
    if (!tidFromNotifyLink(it.link)) return;
    setNotifyOpen(false);
    onOpenNotifyThread?.(it.link, it.title);
  };

  /** 拉关注面板的三份数据(规则/命中/状态),失败只提示不阻塞 */
  const loadWatch = useCallback(async () => {
    setWatchLoading(true);
    try {
      const [rules, hits, status] = await Promise.all([
        sdgunApi.watchRules(),
        sdgunApi.watchHits(-1, 1000),   // 与后端 MAX_HITS 对齐,别让已读记录"最多只有 100"
        sdgunApi.watchStatus(),
      ]);
      const hitList = hits.hits ?? [];
      // 旧数据迁移(hit_id 已读 → tid 已读)必须在读集合之前:幂等,可重复调用
      migrateWatchReadFromHitIds(hitList);
      setWatchRules(rules.rules ?? []);
      setWatchHits(hitList);
      setWatchReadTids(loadWatchReadTids());
      setWatchStatus(status);
    } catch (e) {
      toastErr('关注数据加载失败', e);
    } finally {
      setWatchLoading(false);
    }
  }, []);

  /** 打开关注抽屉:拉数据 + 从 localStorage 读已读帖子集合(角标由它决定,不再"打开即清零") */
  const openWatch = () => {
    setWatchOpen(true);
    setWatchReadTids(loadWatchReadTids());
    void loadWatch();
  };

  /** 记一批**帖子**(tid)为已读;写内存+localStorage 并上报服务端,同步 state,并让 App 重算角标 */
  const markTidsRead = (tids: number[]) => {
    if (tids.length === 0) return;
    const next = reportWatchRead(tids);
    setWatchReadTids(next);
    onWatchRefresh?.();
  };

  /** 改提醒时间窗:POST 后只刷 status(不整抽屉转圈,免得打断浏览) */
  const changeWindowHours = async (hours: number) => {
    try {
      await sdgunApi.watchSettingsSet(hours);
      setWatchStatus(await sdgunApi.watchStatus());
      message.success(`已改为只提醒 ${hours} 小时内发布的新帖`);
    } catch (e) {
      toastErr('设置失败', e);
    }
  };

  const addWatchRule = async () => {
    const keywords = newKeywords.trim();
    if (!keywords) return;
    try {
      const r = await sdgunApi.watchRulesAdd(keywords, newRuleName.trim());
      setWatchRules(r.rules ?? []);
      setNewKeywords('');
      setNewRuleName('');
      message.success('已添加关注规则');
    } catch (e) {
      toastErr('添加失败', e);
    }
  };

  const toggleWatchRule = async (id: number) => {
    setWatchBusyId(id);
    try {
      const r = await sdgunApi.watchRulesToggle(id);
      setWatchRules(r.rules ?? []);
    } catch (e) {
      toastErr('切换失败', e);
    } finally {
      setWatchBusyId(null);
    }
  };

  const removeWatchRule = async (id: number) => {
    setWatchBusyId(id);
    try {
      const r = await sdgunApi.watchRulesRemove(id);
      setWatchRules(r.rules ?? []);
    } catch (e) {
      toastErr('删除失败', e);
    } finally {
      setWatchBusyId(null);
    }
  };

  /** 立即检测:代理侧同步跑一轮轮询,完事刷新三份数据 */
  const runWatchNow = async () => {
    setWatchRunning(true);
    try {
      const r = await sdgunApi.watchRun();
      if (r.ok) message.success(`检测完成：新帖 ${r.new_items} 条,命中 ${r.hits_added} 条`);
      else message.warning(`检测未完成：${r.error ?? '未知原因'}`);
      await loadWatch();
    } catch (e) {
      toastErr('检测失败', e);
    } finally {
      setWatchRunning(false);
    }
  };

  // 已读/未读分类 + 按「发帖时间」倒序(最新发布的帖在最上):
  // 后端 list_hits 是 hit_id 升序游标语义,且 threadList 本身是"最后活动置顶"排序
  // (昨天发的帖因新回复会排到 2 小时前的帖上面),所以必须用 publish_time 重排;
  // 旧记录没有该字段(0)→ 回退 created_at。
  // 已读按**帖子 tid** 判断(不是 hit_id):同一帖子换过 hit_id 也不该"变回未读"。
  const readSet = new Set(watchReadTids);
  const hitTime = (h: WatchHit) => h.publish_time || h.created_at;
  const unreadHits = watchHits.filter((h) => !readSet.has(h.tid));
  const readHits = watchHits.filter((h) => readSet.has(h.tid));
  const shownHits = (watchTab === 'unread' ? unreadHits : readHits)
    .slice()
    .sort((a, b) => hitTime(b) - hitTime(a));

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
      <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
        水弹社区控制台
      </Typography.Title>

      <Space size="middle">
        {loading && <Spin size="small" />}
        {!loading && user && (
          <>
            {/* 点头像/昵称打开自己的主页:动态/帖子/资料/关注列表(2026-09-10 加) */}
            <Space
              size={6}
              style={{ cursor: 'pointer' }}
              title="查看我的主页（含关注列表）"
              onClick={() => onOpenConversation?.(user.user_id, user.name)}
            >
              <Avatar src={proxiedImage(user.head)} />
              <Typography.Text style={{ color: '#fff' }}>{user.name}</Typography.Text>
            </Space>
            <Tag color="blue">{user.group_name}</Tag>
            <Typography.Text style={{ color: '#fff' }} type="secondary">
              user_id: {user.user_id}
            </Typography.Text>
          </>
        )}
        {!loading && !user && (
          <>
            <Alert
              type="warning"
              showIcon
              message="未登录 / Cookie 失效"
              description={error ?? undefined}
              style={{ padding: '2px 8px' }}
            />
            <Button type="primary" onClick={() => { setLoginFailMsg(null); setLoginOpen(true); }}>
              登录
            </Button>
          </>
        )}

        <Badge count={unread} size="small" offset={[-2, 2]}>
          <Button onClick={openConversations}>私信</Button>
        </Badge>

        <Badge count={watchUnseen} size="small" offset={[-2, 2]}>
          <Button onClick={openWatch}>关注</Button>
        </Badge>

        <Popover
          open={notifyOpen}
          trigger="click"
          onOpenChange={(o) => openNotify(o)}
          title="通知中心"
          content={
            <div style={{ width: 400, maxHeight: 420, overflow: 'auto' }}>
              {notifyLoading && <Spin size="small" style={{ display: 'block', margin: '12px auto' }} />}
              {!notifyLoading && notifyItems.length === 0 && (
                <Typography.Text type="secondary">暂无通知</Typography.Text>
              )}
              {!notifyLoading && notifyItems.map(({ code, label, items }) => (
                <div key={code} style={{ marginBottom: 8 }}>
                  <Tag color="blue">{label}</Tag>
                  <List
                    size="small"
                    dataSource={items}
                    rowKey={(it) => `${code}-${it.id}`}
                    renderItem={(it) => (
                      <List.Item
                        style={{ cursor: tidFromNotifyLink(it.link) ? 'pointer' : 'default', padding: '6px 0' }}
                        onClick={() => clickNotifyItem(it)}
                      >
                        <List.Item.Meta
                          title={<Typography.Text style={{ fontSize: 13 }}>{it.content.replace(/\s*查看\s*$/, '')}</Typography.Text>}
                          description={
                            <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                              {it.title || '(无标题)'} · {fmtEpochSec(Number(it.create_time) || 0)}
                            </Typography.Text>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </div>
              ))}
              <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginBottom: 0 }}>
                数据来自上游通知接口；点击条目跳转对应帖子。
              </Typography.Paragraph>
            </div>
          }
        >
          <Badge count={notifyUnseen} size="small" offset={[-2, 2]}>
            <Button>通知</Button>
          </Badge>
        </Popover>

        <Popover
          open={open}
          trigger="click"
          onOpenChange={setOpen}
          title="会话 Cookie"
          content={
            <div style={{ width: 420 }}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                优先用「登录」按钮走账号密码登录（WAP 通道，代理代为提交并接管会话）。
                也可手动粘贴 Cookie（格式：{'3df5d0fc...=token; PHPSESSID=...; server_session_...=...'}），
                留空则由本地代理使用兜底会话。
              </Typography.Paragraph>
              <textarea
                value={cookieDraft}
                onChange={(e) => setCookieDraft(e.target.value)}
                rows={4}
                style={{ width: '100%', fontSize: 12 }}
                placeholder="粘贴抓包里的 cookie 请求头整串"
              />
              <Button type="primary" block style={{ marginTop: 8 }} onClick={saveCookie}>
                保存并刷新（留空 = 使用代理会话）
              </Button>
              <Space style={{ marginTop: 8 }}>
                <Button size="small" onClick={useProxySession}>
                  使用代理会话
                </Button>
                <Button size="small" onClick={() => { setOpen(false); setLoginFailMsg(null); setLoginOpen(true); }}>
                  账号密码登录
                </Button>
                <Button size="small" loading={replaying} onClick={() => void replayLogin()}>
                  重放抓包登录
                </Button>
              </Space>
            </div>
          }
        >
          <Button>会话设置</Button>
        </Popover>

        <Modal
          title="账号密码登录"
          open={loginOpen}
          onCancel={() => setLoginOpen(false)}
          footer={null}
          destroyOnClose
        >
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            走 WAP 登录通道（明文表单，由本机代理转发），成功后代管全局会话。
            密码错误 3 次上游会临时锁定登录，失败提示里的计数请留意。
          </Typography.Paragraph>
          <Form form={loginForm} layout="vertical" onFinish={(v) => void accountLogin(v)}>
            <Form.Item name="account" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
              <Input autoComplete="username" placeholder="用户名 / 手机号" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password autoComplete="current-password" onPressEnter={() => loginForm.submit()} />
            </Form.Item>
            {loginFailMsg && (
              <Alert type="error" showIcon message="登录失败" description={loginFailMsg} style={{ marginBottom: 12 }} />
            )}
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Button type="primary" loading={loggingIn} onClick={() => loginForm.submit()}>
                登录
              </Button>
              <Button loading={replaying} onClick={() => void replayLogin()}>
                用最新抓包重放登录（免输密码）
              </Button>
            </Space>
          </Form>
        </Modal>

        <Drawer
          title="私信会话"
          open={convOpen}
          onClose={() => setConvOpen(false)}
          width={420}
        >
          {convLoading && <Spin style={{ display: 'block', margin: '24px auto' }} />}
          {!convLoading && convs.length === 0 && (
            <Typography.Paragraph type="secondary">
              本地归档中还没有会话。归档自代理部署起累积——打开某位用户主页的私信面板、
              或网页在线期间收到的消息都会进入这里（App 先读走的消息除外，上游限制）。
            </Typography.Paragraph>
          )}
          {!convLoading && (
            <List
              dataSource={convs}
              rowKey={(c) => c.peer_id}
              renderItem={(c) => {
                const unreadDot = !c.last_from_me && c.last_time > readMarkerOf(c.peer_id);
                const uid = Number(c.peer_id.replace('user', ''));
                return (
                  <List.Item
                    style={{ cursor: 'pointer', padding: '10px 4px' }}
                    onClick={() => openConversation(c)}
                  >
                    <List.Item.Meta
                      avatar={
                        <Badge dot={unreadDot} offset={[-4, 30]}>
                          <Avatar>{(c.name || c.peer_id).slice(0, 1).toUpperCase()}</Avatar>
                        </Badge>
                      }
                      title={
                        <Space>
                          <Typography.Text strong>{c.name || `UID ${uid}`}</Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                            {c.count} 条 · {fmtEpochSec(c.last_time)}
                          </Typography.Text>
                        </Space>
                      }
                      description={
                        <Typography.Text
                          type={unreadDot ? undefined : 'secondary'}
                          style={{ fontSize: 13 }}
                          ellipsis
                        >
                          {c.last_from_me ? '我：' : ''}{c.last_text || '(非文本消息)'}
                        </Typography.Text>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          )}
          <Typography.Paragraph type="secondary" style={{ fontSize: 11 }}>
            列表来自代理本地归档（/messages/conversations），点击进入对方主页的私信面板查看完整历史。
          </Typography.Paragraph>
        </Drawer>

        <Drawer
          title="关注关键字"
          open={watchOpen}
          onClose={() => setWatchOpen(false)}
          width={460}
        >
          {watchLoading && <Spin style={{ display: 'block', margin: '24px auto' }} />}
          {!watchLoading && (
            <>
              <Typography.Text strong style={{ fontSize: 13 }}>规则管理</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginBottom: 8 }}>
                同一规则内的多个关键字需全部命中（AND）才提醒；多条规则任一命中即提醒（OR）。
                大小写不敏感；匹配的是帖子标题。
              </Typography.Paragraph>
              <List
                size="small"
                dataSource={watchRules}
                rowKey={(r) => r.id}
                locale={{
                  emptyText: (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      还没有规则，先在下面添加关键字
                    </Typography.Text>
                  ),
                }}
                renderItem={(r) => (
                  <List.Item
                    style={{ padding: '8px 0' }}
                    actions={[
                      <Switch
                        key="sw"
                        size="small"
                        checked={r.enabled}
                        loading={watchBusyId === r.id}
                        onChange={() => void toggleWatchRule(r.id)}
                      />,
                      <Popconfirm
                        key="del"
                        title="删除这条规则？"
                        onConfirm={() => void removeWatchRule(r.id)}
                      >
                        <Button size="small" type="text" danger loading={watchBusyId === r.id}>
                          删除
                        </Button>
                      </Popconfirm>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Typography.Text style={{ fontSize: 13 }}>
                          {r.name || r.keywords.join(' + ')}
                        </Typography.Text>
                      }
                      description={
                        <Space size={4} wrap>
                          {r.keywords.map((k) => (
                            <Tag key={k} style={{ marginRight: 0 }}>{k}</Tag>
                          ))}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
              <Space.Compact style={{ width: '100%', marginTop: 8 }}>
                <Input
                  style={{ width: 110 }}
                  placeholder="名称（可选）"
                  value={newRuleName}
                  onChange={(e) => setNewRuleName(e.target.value)}
                />
                <Input
                  placeholder="多个关键字用逗号或空格分隔，需全部命中才提醒"
                  value={newKeywords}
                  onChange={(e) => setNewKeywords(e.target.value)}
                  onPressEnter={() => void addWatchRule()}
                />
                <Button type="primary" disabled={!newKeywords.trim()} onClick={() => void addWatchRule()}>
                  添加
                </Button>
              </Space.Compact>

              <Space style={{ width: '100%', justifyContent: 'space-between', marginTop: 16 }}>
                <Typography.Text strong style={{ fontSize: 13 }}>最近命中</Typography.Text>
                {watchTab === 'unread' && unreadHits.length > 0 && (
                  <Button size="small" type="link"
                          onClick={() => markTidsRead(unreadHits.map((h) => h.tid))}>
                    全部已读
                  </Button>
                )}
              </Space>
              <Segmented
                size="small"
                block
                style={{ marginBottom: 8 }}
                value={watchTab}
                onChange={(v) => setWatchTab(v as 'unread' | 'read')}
                options={[
                  { label: `未读 (${unreadHits.length})`, value: 'unread' },
                  { label: `已读 (${readHits.length})`, value: 'read' },
                ]}
              />
              <List
                size="small"
                dataSource={shownHits}
                rowKey={(h) => h.hit_id}
                locale={{
                  emptyText: (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {watchTab === 'unread'
                        ? '没有未读命中。命中自部署起累积；首次运行只建立基线，不回溯历史帖。'
                        : '还没有已读记录：点开一条命中即记为已读。'}
                    </Typography.Text>
                  ),
                }}
                renderItem={(h) => (
                  <List.Item
                    style={{ cursor: 'pointer', padding: '8px 0' }}
                    onClick={() => {
                      markTidsRead([h.tid]);          // 点开即已读(按帖子 tid 记)
                      setWatchOpen(false);
                      onOpenWatchThread?.(h.tid, h.title);
                    }}
                  >
                    <List.Item.Meta
                      title={
                        <Typography.Text style={{ fontSize: 13 }} ellipsis>
                          {h.title}
                        </Typography.Text>
                      }
                      description={
                        <Space size={4} wrap>
                          {h.keywords.map((k) => (
                            <Tag key={k} color="blue" style={{ marginRight: 0 }}>{k}</Tag>
                          ))}
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {h.user_name} · 发布 {fmtEpochSec(hitTime(h))}
                          </Typography.Text>
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />

              {watchStatus?.last_error && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 12 }}
                  message="上次检测失败"
                  description={watchStatus.last_error}
                />
              )}
              <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 12 }}>
                监控源：{watchStatus?.source
                  ? (watchStatus.source.fid
                    ? (watchStatus.source.forum_name || `版块 fid ${watchStatus.source.fid}`)
                    : `${watchStatus.source.channel_name || `频道 ${watchStatus.source.channel_id}`} · 分类 ${watchStatus.source.cat_id}`)
                  : '解析中'}
                ；轮询间隔 {watchStatus?.interval ?? '—'} 秒；最后检测：
                {watchStatus && watchStatus.last_ok_at > 0 ? fmtEpochSec(watchStatus.last_ok_at) : '尚未运行'}；
                累计命中 {watchStatus?.hit_count ?? 0} 条；仅提醒 {watchStatus?.window_hours ?? 24}h 内新帖。
              </Typography.Paragraph>
              <Space size={8} wrap style={{ marginBottom: 8 }}>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  只提醒发布时间在
                </Typography.Text>
                <Select
                  size="small"
                  style={{ width: 92 }}
                  value={watchStatus?.window_hours ?? 24}
                  onChange={(v) => void changeWindowHours(v)}
                  options={WATCH_WINDOW_OPTIONS}
                />
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  内的新帖（过滤被翻出的旧帖）
                </Typography.Text>
              </Space>
              <div>
                <Button size="small" loading={watchRunning} onClick={() => void runWatchNow()}>
                  立即检测
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                  监控由代理后台线程执行（与页面是否打开无关），新帖标题命中即提醒。
                </Typography.Text>
              </div>
            </>
          )}
        </Drawer>
      </Space>
    </div>
  );
}
