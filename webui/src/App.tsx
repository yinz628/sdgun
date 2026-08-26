import { useState } from 'react';
import { Layout } from 'antd';
import UserBar from './components/UserBar';
import FeedList from './components/FeedList';
import ThreadPanel from './components/ThreadPanel';
import UserPanel from './components/UserPanel';
import OpsForms from './components/OpsForms';
import type { InfoFeedItem, JsApiUser } from './types/sdgun-api-types';

const { Header, Sider, Content } = Layout;

/** 右侧详情区当前展示的视图：帖子详情 或 用户主页 */
interface UserView {
  userId: number | string;
  name?: string;
}

/** 布局：顶栏用户态，左侧信息流列表，右侧帖子详情/用户主页 + 操作表单 */
export default function App() {
  const [user, setUser] = useState<JsApiUser | null>(null);
  const [selected, setSelected] = useState<InfoFeedItem | null>(null);
  const [userView, setUserView] = useState<UserView | null>(null);

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
        <UserBar onUserChange={setUser} />
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
