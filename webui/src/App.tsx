import { useEffect, useRef, useState } from 'react';
import { Layout, notification } from 'antd';
import UserBar from './components/UserBar';
import FeedList from './components/FeedList';
import ThreadPanel from './components/ThreadPanel';
import UserPanel from './components/UserPanel';
import OpsForms from './components/OpsForms';
import { sdgunApi } from './api/sdgun';
import type { InfoFeedItem, JsApiUser } from './types/sdgun-api-types';

const { Header, Sider, Content } = Layout;

/** 右侧详情区当前展示的视图：帖子详情 或 用户主页 */
interface UserView {
  userId: number | string;
  name?: string;
}

/** 全局私信监听:登录后每 15s 轮询一次 unReadMsg,新消息做三件事:
 *  ① 经 sdgun-incoming-msgs 事件分发给打开中的会话面板;
 *  ② 代理侧已归档(打开会话时可从 /messages/local 取回);
 *  ③ 非当前打开对端的来信用 notification 弹提醒并累计顶栏角标。
 *  轮询消费未读是有意为之:消费前先归档,消息不会丢(App 先读走的除外,上游限制)。
 */
function useIncomingMsgWatcher(me: JsApiUser | null,
                               userViewRef: React.RefObject<UserView | null>,
                               onIncoming: (count: number) => void) {
  useEffect(() => {
    if (!me) return;
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | null = null;
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
            let sender = m.from_user_id;
            let text = m.content;
            try {
              const ext = m.ext ? JSON.parse(m.ext) : null;
              if (ext?.user_name) sender = ext.user_name;
              text = JSON.parse(m.content)?.text ?? m.content;
            } catch { /* 原样展示 */ }
            notification.open({
              message: `私信 · ${sender}`,
              description: String(text).slice(0, 80),
              placement: 'bottomRight',
              duration: 4,
            });
          }
        }
      } catch { /* 上游偶发慢响应/断连,静默等下一轮 */ }
    };

    void poll();
    timer = setInterval(() => void poll(), 15000);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [me, userViewRef, onIncoming]);
}

/** 布局：顶栏用户态，左侧信息流列表，右侧帖子详情/用户主页 + 操作表单 */
export default function App() {
  const [user, setUser] = useState<JsApiUser | null>(null);
  const [selected, setSelected] = useState<InfoFeedItem | null>(null);
  const [userView, setUserView] = useState<UserView | null>(null);
  const [unread, setUnread] = useState(0);
  const userViewRef = useRef<UserView | null>(null);
  userViewRef.current = userView;

  useIncomingMsgWatcher(user, userViewRef, (n) => setUnread((c) => c + n));

  const openUser = (userId: number | string, name?: string) =>
    setUserView({ userId, name });

  return (
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
                me={user ? { user_id: user.user_id, name: user.name, head: user.head } : null}
                onClose={() => setUserView(null)}
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
  );
}
