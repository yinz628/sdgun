import { useCallback, useEffect, useRef, useState } from 'react';
import { Input, Layout, Modal, notification } from 'antd';
import UserBar from './components/UserBar';
import FeedList from './components/FeedList';
import ThreadPanel from './components/ThreadPanel';
import UserPanel from './components/UserPanel';
import OpsForms from './components/OpsForms';
import { sdgunApi } from './api/sdgun';
import {
  NOTIFY_LABEL, fmtEpochSec, isWatchTidRead, migrateWatchReadFromHitIds, tidFromNotifyLink,
} from './utils/notify';
import { chatMsgExcerpt } from './utils/chat';
import { setToken, TOKEN_REQUIRED_EVENT, tokenEvents } from './utils/token';
import type { InfoFeedItem, JsApiUser } from './types/sdgun-api-types';

const { Header, Sider, Content } = Layout;

/** 右侧详情区当前展示的视图：帖子详情 或 用户主页 */
interface UserView {
  userId: number | string;
  name?: string;
  openChat?: boolean;
}

/**
 * 可见性门控的周期轮询：仅当页面可见时运行 interval，隐藏时清掉定时器
 * （不让 interval 空转后再判断 visibility）；切回可见立即补拉一次，不等下一个周期。
 *
 * 返回停止函数，effect cleanup 调用即移除定时器与 visibilitychange 监听。
 * 每次调用独立持有 timer/监听器：React 18 StrictMode(dev)的挂载→卸载→再挂载
 * 会各建各清，不会泄漏或重复。
 */
function startVisibilityPolling(poll: () => void, intervalMs: number): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  const start = () => {
    if (timer == null) timer = setInterval(poll, intervalMs);
  };
  const stop = () => {
    if (timer != null) { clearInterval(timer); timer = null; }
  };
  const onVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
      stop();       // 先清掉隐藏期间可能残留的定时器，再补拉一次
      poll();
      start();      // 重新计时：下一个周期从"切回可见"起算
    } else {
      stop();       // 隐藏即停，零轮询
    }
  };
  if (document.visibilityState === 'visible') start();
  document.addEventListener('visibilitychange', onVisibilityChange);
  return () => {
    stop();
    document.removeEventListener('visibilitychange', onVisibilityChange);
  };
}

/** 全局私信监听:登录后每 15s 轮询一次 unReadMsg,新消息做三件事:
 *  ① 经 sdgun-incoming-msgs 事件分发给打开中的会话面板;
 *  ② 代理侧已归档(打开会话时可从 /messages/local 取回);
 *  ③ 非当前打开对端的来信用 notification 弹提醒并累计顶栏角标,点击直达对话。
 *  轮询消费未读是有意为之:消费前先归档,消息不会丢(App 先读走的除外,上游限制)。
 */
function useIncomingMsgWatcher(me: JsApiUser | null,
                               userViewRef: React.RefObject<UserView | null>,
                               onIncoming: (count: number) => void,
                               onOpenConversation: (uid: number | string, name?: string, openChat?: boolean) => void) {
  useEffect(() => {
    if (!me) return;
    let stopped = false;
    let chatToken = '';
    const seen = new Set<number>();

    const poll = async () => {
      if (stopped) return;
      try {
        if (!chatToken) {
          const rt = await sdgunApi.repairChatToken(me.user_id);
          chatToken = rt.data?.chatToken ?? '';
          if (!chatToken) return;
        }
        const ur = await sdgunApi.chatUnread(chatToken, me.user_id);
        const fresh = (ur.msgs ?? []).filter((m) => !seen.has(m.msg_id));
        if (fresh.length === 0) return;
        fresh.forEach((m) => seen.add(m.msg_id));
        window.dispatchEvent(new CustomEvent('sdgun-incoming-msgs', { detail: fresh }));

        const openPeer = userViewRef.current?.userId != null
          ? `user${userViewRef.current.userId}` : '';
        const incoming = fresh.filter((m) =>
          m.from_user_id !== `user${me.user_id}` && m.from_user_id !== openPeer);
        if (incoming.length > 0) {
          onIncoming(incoming.length);
          for (const m of incoming) {
            let senderName = '';
            const text = chatMsgExcerpt(m.content) || '（暂不支持的消息类型）';
            try {
              const ext = m.ext ? JSON.parse(m.ext) : null;
              if (ext?.user_name) senderName = ext.user_name;
            } catch { /* 原样展示 */ }
            const uid = Number(String(m.from_user_id ?? '').replace('user', ''));
            const key = `sdgun-dm-${m.msg_id}`;
            notification.open({
              key,
              message: `私信 · ${senderName || m.from_user_id}`,
              description: String(text).slice(0, 80),
              placement: 'bottomRight',
              duration: 6,
              onClick: () => {
                notification.destroy(key);
                if (Number.isFinite(uid) && uid > 0) onOpenConversation(uid, senderName || undefined, true);
              },
            });
          }
        }
      } catch { /* 上游偶发慢响应/断连,静默等下一轮 */ }
    };

    void poll();
    // 可见性门控:页面隐藏时零轮询(interval 直接清掉,不空转判断),切回可见立即补拉
    const stopPolling = startVisibilityPolling(() => void poll(), 15000);
    return () => {
      stopped = true;
      stopPolling();
    };
  }, [me, userViewRef, onIncoming, onOpenConversation]);
}

/**
 * 通知中心监听:登录后每 60s 轮询 userNotifyIndex(各分类未读计数,实测轮询不清零)。
 * 已读状态存 localStorage(sdgunNotifySeen: code → 最后看到的 create_time):
 * 首次运行只建立基线不弹提醒;之后出现更新的 create_time 即弹提醒并累计角标,
 * 用户打开「通知」面板(markRead)后清零。上游无清除接口,计数由本地管理。
 */
function useNotifyWatcher(me: JsApiUser | null) {
  const [unseen, setUnseen] = useState(0);
  const lastItemsRef = useRef<{ code: string; t: number; content: string }[]>([]);
  const markReadRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!me) { setUnseen(0); return; }
    let stopped = false;
    const KEY = 'sdgunNotifySeen';

    const loadSeen = (): Record<string, number> => {
      try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch { return {}; }
    };
    const saveSeen = (s: Record<string, number>) =>
      localStorage.setItem(KEY, JSON.stringify(s));

    const poll = async (silent: boolean) => {
      if (stopped) return;
      try {
        const resp = await sdgunApi.notifyIndex();
        const items = resp.data?.notifyList ?? [];
        const seen = loadSeen();
        const firstRun = seen.__init !== 1;
        let fresh = 0;
        lastItemsRef.current = [];
        for (const it of items) {
          const t = Number(it.create_time) || 0;
          if (t > 0) lastItemsRef.current.push({ code: it.code, t, content: it.content });
          if (t > (seen[it.code] ?? 0)) {
            seen[it.code] = t;
            if (!firstRun && !silent) {
              fresh += 1;
              notification.open({
                message: `通知 · ${NOTIFY_LABEL[it.code] ?? it.code}`,
                description: it.content.replace(/\s*查看\s*$/, ''),
                placement: 'bottomRight',
                duration: 6,
              });
            }
          }
        }
        seen.__init = 1;
        saveSeen(seen);
        if (!silent) setUnseen(fresh);
      } catch { /* 上游偶发慢响应,静默等下一轮 */ }
    };

    markReadRef.current = () => {
      const seen = loadSeen();
      for (const it of lastItemsRef.current) seen[it.code] = Math.max(seen[it.code] ?? 0, it.t);
      seen.__init = 1;
      saveSeen(seen);
      setUnseen(0);
    };

    void poll(true);   // 首轮静默建立基线,不拿历史通知轰炸用户
    // 可见性门控:隐藏时零轮询,切回可见立即补拉一次(通知为非消费型接口,补拉安全)
    const stopPolling = startVisibilityPolling(() => void poll(false), 60000);
    return () => {
      stopped = true;
      stopPolling();
    };
  }, [me]);

  const markRead = useCallback(() => markReadRef.current(), []);
  return { unseen, markRead };
}

/**
 * 关注关键字监控监听：每 30s 拉一次代理侧的命中记录（代理后台线程轮询信息流，
 * 与页面是否打开无关，因此**不依赖登录态**，挂载即轮询）。
 * 两条互不相干的本地状态：
 *  - sdgunWatchSeen：通知游标（最后已弹过提醒的 hit_id）。本地无游标（首次运行）→
 *    只记基线不弹，避免拿历史命中轰炸用户；之后出现更大的 hit_id 即弹右下角通知。
 *  - sdgunWatchReadTids：已读帖子集合（notify.ts，按 tid）。角标 unseen 直接由"本轮命中
 *    里 tid 未读的数量"算出——持久化在浏览器，刷新页面不再清零，也不会被"打开面板"误清。
 *    按 tid 而非 hit_id：同一帖子会因去重/裁剪/重现换 hit_id，阅读属性属于帖子本身。
 *    每轮顺手调用 migrateWatchReadFromHitIds(hits) 把旧 hit_id 已读迁到 tid（幂等，
 *    helpers 不依赖是否打开过抽屉）。
 * refreshUnseen() 供 UserBar 标记已读/全部已读后立即刷新角标。
 */
function useWatchHits(onOpenThread: (tid: number, title: string) => void) {
  const [unseen, setUnseen] = useState(0);
  const pollRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    const KEY = 'sdgunWatchSeen';

    /** null = 本地还没有游标（首次运行） */
    const loadSeen = (): number | null => {
      const raw = localStorage.getItem(KEY);
      if (raw == null) return null;
      const n = Number(raw);
      return Number.isFinite(n) && n >= 0 ? n : null;
    };
    const saveSeen = (v: number) => localStorage.setItem(KEY, String(v));

    const poll = async () => {
      if (stopped) return;
      try {
        const seen = loadSeen();
        // 取最新 1000 条(与后端 MAX_HITS 对齐;而非"仅游标之后"):角标要覆盖所有未读命中
        const resp = await sdgunApi.watchHits(-1, 1000);
        const hits = resp.hits ?? [];
        const maxId = Number(resp.max_hit_id) || 0;
        // 先迁移旧 key（hit_id → tid），再算角标，否则升级后的首轮会全部显示未读
        migrateWatchReadFromHitIds(hits);
        setUnseen(hits.filter((h) => !isWatchTidRead(h.tid)).length);
        if (seen == null) {                 // 首轮：静默建立通知游标
          saveSeen(maxId);
          return;
        }
        const fresh = hits.filter((h) => h.hit_id > seen);
        if (fresh.length === 0) {
          if (maxId > seen) saveSeen(maxId);   // 命中已被裁剪等情形，游标仍要推进
          return;
        }
        // 每轮最多弹 20 条（与旧行为一致）：游标只推进到本批最后一条，剩下的下一轮再弹，
        // 避免长时间停机/换源后一次性弹上百条通知
        const batch = fresh.slice(0, 20);
        for (const hit of batch) {
          const key = `sdgun-watch-${hit.hit_id}`;
          notification.open({
            key,
            message: `关注提醒 · ${hit.keywords.join(' + ')}`,
            description: `${hit.title}（${hit.user_name} · ${fmtEpochSec(hit.created_at)}）`,
            placement: 'bottomRight',
            duration: 8,
            onClick: () => {
              notification.destroy(key);
              onOpenThread(hit.tid, hit.title);
            },
          });
        }
        saveSeen(batch[batch.length - 1].hit_id);
      } catch { /* 代理/上游偶发异常，静默等下一轮 */ }
    };

    pollRef.current = poll;

    void poll();
    timer = setInterval(() => void poll(), 30000);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [onOpenThread]);

  const refreshUnseen = useCallback(() => { void pollRef.current(); }, []);
  return { unseen, refreshUnseen };
}

/** 布局：顶栏用户态，左侧信息流列表，右侧帖子详情/用户主页 + 操作表单 */
export default function App() {
  const [user, setUser] = useState<JsApiUser | null>(null);
  const [selected, setSelected] = useState<InfoFeedItem | null>(null);
  const [userView, setUserView] = useState<UserView | null>(null);
  const [unread, setUnread] = useState(0);
  const [tokenPrompt, setTokenPrompt] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');
  const userViewRef = useRef<UserView | null>(null);
  userViewRef.current = userView;

  /** 代理访问令牌门禁:任一带业务请求收到门禁 401(api/client 派发事件)即弹窗。
   *  保存后整页 reload,让所有初始请求带上新令牌重放——最简可靠,不做请求级重试。 */
  useEffect(() => {
    const onTokenRequired = () => setTokenPrompt(true);
    tokenEvents.addEventListener(TOKEN_REQUIRED_EVENT, onTokenRequired);
    return () => tokenEvents.removeEventListener(TOKEN_REQUIRED_EVENT, onTokenRequired);
  }, []);

  const submitToken = () => {
    const v = tokenDraft.trim();
    if (!v) return;
    setToken(v);
    window.location.reload();
  };

  const { unseen: notifyUnseen, markRead: markNotifyRead } = useNotifyWatcher(user);

  /** openChat: 进入主页即展开私信面板(会话列表/新私信提醒点进来时) */
  const openUser = useCallback((userId: number | string, name?: string, openChat = false) =>
    setUserView({ userId, name, openChat }), []);
  // useCallback 固定引用:进 watcher 依赖数组后,unread 等 state 变化不会再
  // 触发 effect 重建(否则 seen 去重集合被清空、chatToken 作废重取)
  const handleIncoming = useCallback((n: number) => setUnread((c) => c + n), []);
  useIncomingMsgWatcher(user, userViewRef, handleIncoming, openUser);

  /** 从通知明细跳帖子:用 link 里的 tid 构造最小 InfoFeedItem(ThreadPanel 按 id 拉正文) */
  const openThreadFromNotify = useCallback((link: string, title: string) => {
    const tid = tidFromNotifyLink(link);
    if (!tid) return;
    setUserView(null);
    setSelected({ id: tid, title, user_name: '' } as InfoFeedItem);
  }, []);

  /** 关注提醒跳帖子:命中记录里已带 tid,直接构造最小 InfoFeedItem */
  const openWatchThread = useCallback((tid: number, title: string) => {
    setUserView(null);
    setSelected({ id: tid, title, user_name: '' } as InfoFeedItem);
  }, []);

  const { unseen: watchUnseen, refreshUnseen: refreshWatchUnseen } = useWatchHits(openWatchThread);

  return (
    <>
      <Layout style={{ height: '100vh' }}>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            background: '#001529',
            paddingInline: 24,
          }}
        >
          <UserBar
            onUserChange={setUser}
            unread={unread}
            onClearUnread={() => setUnread(0)}
            notifyUnseen={notifyUnseen}
            onNotifyRead={markNotifyRead}
            onOpenConversation={openUser}
            onOpenNotifyThread={openThreadFromNotify}
            watchUnseen={watchUnseen}
            onWatchRefresh={refreshWatchUnseen}
            onOpenWatchThread={openWatchThread}
          />
        </Header>

        <Layout>
          <Sider width={480} theme="light" style={{ padding: 12, overflow: 'auto' }}>
            <FeedList selectedId={selected?.id} onSelect={setSelected} onOpenUser={openUser} />
          </Sider>

          <Content style={{ padding: 12, overflow: 'auto' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minHeight: '100%' }}>
              {userView ? (
                <UserPanel
                  userId={userView.userId}
                  nameHint={userView.name}
                  autoOpenChat={userView.openChat}
                  me={user ? { user_id: user.user_id, name: user.name, head: user.head } : null}
                  onClose={() => setUserView(null)}
                  onOpenUser={openUser}
                  onOpenThread={(item) => { setSelected(item); setUserView(null); }}
                />
              ) : (
                <ThreadPanel item={selected} onOpenUser={openUser} />
              )}
              <OpsForms user={user} />
            </div>
          </Content>
        </Layout>
      </Layout>

      <Modal
        open={tokenPrompt}
        title="需要代理访问令牌"
        okText="保存并重新加载"
        cancelText="稍后"
        okButtonProps={{ disabled: !tokenDraft.trim() }}
        onOk={submitToken}
        onCancel={() => setTokenPrompt(false)}
      >
        <p style={{ marginTop: 0 }}>
          代理已启用访问令牌门禁（部署端设置了 <code>SDGUN_ACCESS_TOKEN</code>），
          请输入部署时配置的访问令牌。
        </p>
        <p style={{ color: '#888', marginBottom: 8 }}>
          这是<strong>代理访问令牌</strong>（用于放行本代理），不是论坛账号密码，
          也不是论坛登录态（Cookie）。
        </p>
        <Input.Password
          autoFocus
          placeholder="访问令牌"
          value={tokenDraft}
          onChange={(e) => setTokenDraft(e.target.value)}
          onPressEnter={submitToken}
        />
      </Modal>
    </>
  );
}
