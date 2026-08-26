import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Card, Col, Descriptions, Form, InputNumber, Row, Select, Space, Tag, Typography, message } from 'antd';
import { ApiError, api } from '../api/client';
import { sdgunApi } from '../api/sdgun';
import type { JsApiUser } from '../types/sdgun-api-types';

/** 代理侧签到接口（非上游业务包裹，无 success 字段） */
interface SignRunResult {
  ok: boolean;
  already?: boolean;
  msg?: string;
  date?: string;
  manual?: boolean;
  ran_at?: string;
  continuous_day?: number;
  today_count?: number;
  reward?: { reward_type?: number; gold?: { baseGoldReward?: string; extraGoldReward?: number } };
}

interface SignStatus {
  auto_sign: boolean;
  sign_time: string;
  tz_offset: number;
  today: string;
  today_signed: boolean;
  live: { signed: boolean | null; continuous_day: number | null; today_count: number | null } | null;
  last_run?: SignRunResult;
}

/** 右侧下：操作表单（每日阅读上报 / 聊天 Token / 每日签到） */
export default function OpsForms({ user }: { user: JsApiUser | null }) {
  const [readSubmitting, setReadSubmitting] = useState(false);
  const [chatSubmitting, setChatSubmitting] = useState(false);
  const [unreadLoading, setUnreadLoading] = useState(false);
  const [chatToken, setChatToken] = useState<string | null>(null);
  const [unread, setUnread] = useState<number | null>(null);

  const [signStatus, setSignStatus] = useState<SignStatus | null>(null);
  const [signLoading, setSignLoading] = useState(false);
  const [signRunning, setSignRunning] = useState(false);

  const loadSignStatus = async () => {
    setSignLoading(true);
    try {
      const resp = await api.get<SignStatus>('/sign/status');
      setSignStatus(resp.data);
    } catch (e) {
      message.error(`签到状态获取失败：${(e as ApiError).message}`);
    } finally {
      setSignLoading(false);
    }
  };

  // 挂载时拉一次签到面板状态
  useEffect(() => { void loadSignStatus(); }, []);

  const runSign = async () => {
    setSignRunning(true);
    try {
      const resp = await api.post<SignRunResult>('/sign/run');
      const r = resp.data;
      if (r.ok) {
        message.success(`${r.already ? '今日已签到' : '签到成功'}${r.continuous_day != null ? `，连续 ${r.continuous_day} 天` : ''}`);
      } else {
        message.warning(r.msg || '签到失败');
      }
      await loadSignStatus();
    } catch (e) {
      message.error(`签到失败：${(e as ApiError).message}`);
    } finally {
      setSignRunning(false);
    }
  };

  /** 表单 1：每日阅读任务上报 */
  const onReadFinish = async () => {
    setReadSubmitting(true);
    try {
      const resp = await sdgunApi.dayReadFinish();
      message.success(resp.is_finish ? '今日阅读任务已完成' : '上报成功');
    } catch (e) {
      message.error(`上报失败：${(e as ApiError).message}`);
    } finally {
      setReadSubmitting(false);
    }
  };

  /** 表单 2：换取聊天 token（user_id 默认当前登录用户） */
  const onChatFinish = async (values: { user_id: number }) => {
    setChatSubmitting(true);
    try {
      const resp = await sdgunApi.repairChatToken(values.user_id);
      const token = resp.data.chatToken;
      setChatToken(token);
      setUnread(null);
      message.success(`chatToken 已获取：${token.slice(0, 8)}…`);
    } catch (e) {
      message.error(`获取 chatToken 失败：${(e as ApiError).message}`);
    } finally {
      setChatSubmitting(false);
    }
  };

  const queryUnread = async () => {
    if (!chatToken || !user) return;
    setUnreadLoading(true);
    try {
      const resp = await sdgunApi.chatUnread(chatToken, user.user_id);
      setUnread(resp.unRead);
      message.info(`当前未读私信：${resp.unRead}`);
    } catch (e) {
      message.error(`查询未读失败：${(e as ApiError).message}`);
    } finally {
      setUnreadLoading(false);
    }
  };

  return (
    <Row gutter={12}>
      <Col span={10}>
        <Card title="每日任务" size="small">
          {!user && <Alert type="warning" showIcon message="未登录，提交可能失败" style={{ marginBottom: 12 }} />}
          <Form layout="vertical" onFinish={onReadFinish} initialValues={{ type: 'readContent' }}>
            <Form.Item label="任务类型" name="type" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'readContent', label: 'readContent（阅读内容）' },
                ]}
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={readSubmitting} block>
              提交每日阅读
            </Button>
          </Form>
        </Card>
      </Col>

      <Col span={14}>
        <Card
          title="聊天 Token / 未读私信"
          size="small"
          extra={
            <Space>
              {unread !== null && <Badge count={unread} offset={[6, -2]}>私信</Badge>}
              <Button size="small" loading={unreadLoading} disabled={!chatToken} onClick={queryUnread}>
                查询未读
              </Button>
            </Space>
          }
        >
          <Form layout="vertical" onFinish={onChatFinish} initialValues={{ user_id: user?.user_id }}>
            <Form.Item label="user_id" name="user_id" rules={[{ required: true, message: '请输入 user_id' }]}>
              <InputNumber min={1} style={{ width: '100%' }} placeholder="当前登录用户 ID" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={chatSubmitting} block>
              换取 chatToken
            </Button>
          </Form>
          {chatToken && (
            <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8, display: 'block' }}>
              chatToken：{chatToken.slice(0, 8)}…（已缓存，点“查询未读”使用）
            </Typography.Text>
          )}
        </Card>
      </Col>

      <Col span={24}>
        <Card
          title="每日签到（自动）"
          size="small"
          extra={
            <Space>
              <Tag color={signStatus?.auto_sign ? 'green' : 'default'}>
                {signStatus?.auto_sign
                  ? `自动签到 每日 ${signStatus.sign_time}（UTC+${signStatus.tz_offset}）`
                  : '自动签到未开启'}
              </Tag>
              <Button size="small" loading={signLoading} onClick={() => void loadSignStatus()}>
                刷新状态
              </Button>
              <Button size="small" type="primary" ghost loading={signRunning} onClick={() => void runSign()}>
                立即签到
              </Button>
            </Space>
          }
        >
          <Descriptions
            size="small"
            column={4}
            items={[
              {
                key: 'today',
                label: '今日',
                children: signStatus
                  ? signStatus.today_signed
                    ? <Badge status="success" text="已签到" />
                    : <Badge status="warning" text="未签到" />
                  : '-',
              },
              {
                key: 'continuous',
                label: '连续签到',
                children: signStatus?.live?.continuous_day != null ? `${signStatus.live.continuous_day} 天` : '-',
              },
              {
                key: 'count',
                label: '今日全站已签',
                children: signStatus?.live?.today_count != null ? `${signStatus.live.today_count} 人` : '-',
              },
              {
                key: 'last',
                label: '最近一次',
                children: signStatus?.last_run
                  ? `${signStatus.last_run.manual ? '手动' : '自动'} · ${signStatus.last_run.ran_at ?? ''} · ${signStatus.last_run.msg ?? ''}`
                  : '尚未执行',
              },
            ]}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            由代理守护线程每日自动执行（服务重启会补签当天）；奖励规则：每天 1 歼敌，连签 7 天 +3、一个月 +20、一年 +100。
          </Typography.Text>
        </Card>
      </Col>
    </Row>
  );
}
