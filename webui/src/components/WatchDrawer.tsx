import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Drawer, Input, List, Popconfirm, Segmented, Select, Space, Spin, Switch, Tag, Typography, message } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { fmtEpochSec, loadWatchReadTids, migrateWatchReadFromHitIds } from '../utils/notify';
import { reportWatchRead } from '../utils/uistate';
import { toastErr } from '../utils/toast';
import type { WatchHit, WatchRule, WatchStatus } from '../types/sdgun-api-types';

/** 提醒时间窗选项(与后端 WINDOW_HOURS_CHOICES 一致) */
const WATCH_WINDOW_OPTIONS = [12, 24, 48].map((h) => ({ value: h, label: `${h} 小时` }));

interface Props {
  open: boolean;
  onClose: () => void;
  /** 标记已读后让 App 重算未读角标 */
  onWatchRefresh?: () => void;
  /** 从命中记录跳转帖子 */
  onOpenThread?: (tid: number, title: string) => void;
}

/** 关注关键字监控抽屉（规则管理 + 最近命中 + 状态；立即检测） */
export default function WatchDrawer({ open, onClose, onWatchRefresh, onOpenThread }: Props) {
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

  /** 打开抽屉:拉数据 + 从 localStorage 读已读帖子集合(角标由它决定,不再"打开即清零") */
  useEffect(() => {
    if (!open) return;
    setWatchReadTids(loadWatchReadTids());
    void loadWatch();
  }, [open, loadWatch]);

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
    <Drawer
      title="关注关键字"
      open={open}
      onClose={onClose}
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
                  onClose();
                  onOpenThread?.(h.tid, h.title);
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
  );
}
