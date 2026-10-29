import { useEffect, useState } from 'react';
import { Badge, Button, List, Popover, Spin, Tag, Typography } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { NOTIFY_LABEL, fmtEpochSec, tidFromNotifyLink } from '../utils/notify';
import { toastErr } from '../utils/toast';
import type { NotifyItem } from '../types/sdgun-api-types';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 通知中心未读数(回复/@/赞/关注;打开通知面板清零) */
  notifyUnseen?: number;
  /** 打开面板即清本地未读角标 */
  onNotifyRead?: () => void;
  /** 从通知明细跳转帖子 */
  onOpenThread?: (link: string, title: string) => void;
}

/** 通知中心 Popover（分类明细列表；打开即清未读角标） */
export default function NotifyCenter({ open, onOpenChange, notifyUnseen = 0, onNotifyRead, onOpenThread }: Props) {
  const [notifyItems, setNotifyItems] = useState<{ code: string; label: string; items: NotifyItem[] }[]>([]);
  const [notifyLoading, setNotifyLoading] = useState(false);

  /** 打开通知中心:拉各分类明细(只拉有未读的,最多 4 类),同时清本地未读角标 */
  useEffect(() => {
    if (!open) return;
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
  }, [open, onNotifyRead]);

  /** 点击通知条目:跳对应帖子 */
  const clickNotifyItem = (it: NotifyItem) => {
    if (!tidFromNotifyLink(it.link)) return;
    onOpenChange(false);
    onOpenThread?.(it.link, it.title);
  };

  return (
    <Popover
      open={open}
      trigger="click"
      onOpenChange={onOpenChange}
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
  );
}
