import { useCallback, useEffect, useState } from 'react';
import { Avatar, Button, Card, Empty, Image, Input, Pagination, Popconfirm, Space, Spin, Tag, Typography, message } from 'antd';
import { pickData, ApiError } from '../api/client';
import { sdgunApi } from '../api/sdgun';
import { prepareCommentHtml, proxiedImage, threadTid, extractThreadRow } from '../utils/media';
import type { CommentItem, InfoFeedItem, RewardContent, RewardRecordItem, ThreadRowData } from '../types/sdgun-api-types';

const C_STEP = 10;

interface Props {
  item: InfoFeedItem | null;
  /** 点击作者/评论用户头像 → 打开用户主页 */
  onOpenUser: (userId: number | string, name?: string) => void;
}

/** 右侧上：选中帖子的详情（作者栏/正文/评论分页） */
export default function ThreadPanel({ item, onOpenUser }: Props) {
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [cLoading, setCLoading] = useState(false);
  const [cPage, setCPage] = useState(1);
  const [cTotal, setCTotal] = useState(0);
  const [reward, setReward] = useState<unknown[]>([]);
  const [rLoading, setRLoading] = useState(false);
  const [rewardRecords, setRewardRecords] = useState<RewardRecordItem[]>([]);
  const [rrLoading, setRrLoading] = useState(false);
  const [contentHtml, setContentHtml] = useState('');
  const [contentLoading, setContentLoading] = useState(false);
  /** 楼主信息（threadViewPage 的 var row 里有 user_id/user_name/user_head） */
  const [author, setAuthor] = useState<{ id: string; name: string; head: string } | null>(null);
  /** 发评论用的 circle_id（var row.circle_id） */
  const [circleId, setCircleId] = useState<string | number>('');
  /** 评论输入框 */
  const [commentText, setCommentText] = useState('');
  const [commentSubmitting, setCommentSubmitting] = useState(false);
  const [liked, setLiked] = useState(false);
  const [liking, setLiking] = useState(false);
  const [followed, setFollowed] = useState(false);
  const [following, setFollowing] = useState(false);

  const loadComments = useCallback(async (tid: number, p: number) => {
    setCLoading(true);
    try {
      const resp = await sdgunApi.comments(tid, p, C_STEP);
      setComments(resp.list ?? []);
      setCTotal(resp.count ?? 0);
    } catch (e) {
      setComments([]);
      message.error(`评论加载失败：${(e as ApiError).message}`);
    } finally {
      setCLoading(false);
    }
  }, []);

  // 切换帖子时重置并加载第一页评论 + 帖子正文（正文与楼主信息都在 threadViewPage 的 var row 里）
  useEffect(() => {
    setComments([]);
    setReward([]);
    setRewardRecords([]);
    setCPage(1);
    setCTotal(0);
    setLiked(false);
    setFollowed(false);
    setContentHtml('');
    setAuthor(null);
    setCircleId('');
    setCommentText('');
    if (item) {
      const tid = threadTid(item);
      void loadComments(tid, 1);
      setContentLoading(true);
      sdgunApi
        .fetchThreadHtml(tid)
        .then((html) => {
          const row: ThreadRowData | null = extractThreadRow(html);
          setContentHtml(prepareCommentHtml(row?.content ?? ''));
          if (row?.user_id) {
            setAuthor({
              id: String(row.user_id),
              name: String(row.user_name ?? item.user_name ?? '楼主'),
              head: String(row.user_head ?? ''),
            });
          }
          if (row?.circle_id != null) setCircleId(row.circle_id as string | number);
        })
        .catch((e) => message.error(`正文加载失败：${(e as ApiError).message}`))
        .finally(() => setContentLoading(false));
    }
  }, [item, loadComments]);

  /** 发表评论（成功后回到第 1 页刷新，新评论在最前） */
  const doComment = async () => {
    if (!item || !commentText.trim()) return;
    setCommentSubmitting(true);
    try {
      const resp = await sdgunApi.addComment(threadTid(item), commentText.trim(), circleId);
      message.success(resp.data?.message_result === 'post_reply_succeed' ? '评论发表成功' : '评论已提交');
      setCommentText('');
      setCPage(1);
      void loadComments(threadTid(item), 1);
    } catch (e) {
      message.error(`评论失败：${(e as ApiError).message}`);
    } finally {
      setCommentSubmitting(false);
    }
  };

  const loadReward = async () => {
    if (!item) return;
    setRLoading(true);
    try {
      const resp = await sdgunApi.reward(threadTid(item));
      const data = pickData<RewardContent>(resp);
      setReward(data);
      message.success(
        data.length ? `红包内容 ${data.length} 条` : '该帖无红包/悬赏内容',
      );
    } catch (e) {
      message.error(`红包查询失败：${(e as ApiError).message}`);
    } finally {
      setRLoading(false);
    }
  };

  /** 帖子点赞（content_id = tid；code=101 表示已点过） */
  const doLike = async () => {
    if (!item) return;
    setLiking(true);
    try {
      const resp = await sdgunApi.applaudContent(threadTid(item));
      setLiked(true);
      message.success(resp.code === 100 ? '点赞成功' : '该帖子已点赞过');
    } catch (e) {
      message.error(`点赞失败：${(e as ApiError).message}`);
    } finally {
      setLiking(false);
    }
  };

  /** 关注楼主 */
  const doFollowAuthor = async () => {
    if (!item) return;
    setFollowing(true);
    try {
      const resp = await sdgunApi.followUser(item.user_id!);
      setFollowed(true);
      message.success(resp.code === 100 ? `已关注 ${item.user_name ?? '楼主'}` : '该用户之前已关注');
    } catch (e) {
      message.error(`关注失败：${(e as ApiError).message}`);
    } finally {
      setFollowing(false);
    }
  };

  /** 打赏记录（type=forum, type_value=tid） */
  const loadRewardRecords = async () => {
    if (!item) return;
    setRrLoading(true);
    try {
      const resp = await sdgunApi.rewardRecord(threadTid(item));
      const list = resp.list ?? [];
      setRewardRecords(list);
      message.success(list.length ? `打赏记录 ${list.length} 条` : '该帖暂无打赏');
    } catch (e) {
      message.error(`打赏查询失败：${(e as ApiError).message}`);
    } finally {
      setRrLoading(false);
    }
  };

  if (!item) {
    return (
      <Card size="small">
        <Empty description="点击左侧列表中的帖子查看详情" style={{ padding: 32 }} />
      </Card>
    );
  }

  return (
    <Card
      title={`帖子详情 · tid=${item ? threadTid(item) : ''}`}
      size="small"
      extra={
        <Space>
          <Button size="small" loading={rLoading} onClick={loadReward}>
            查询红包/悬赏
          </Button>
          <Button size="small" loading={rrLoading} onClick={loadRewardRecords}>
            查询打赏
          </Button>
        </Space>
      }
    >
      <Typography.Title level={5} style={{ marginTop: 0 }}>{item.title}</Typography.Title>

      {/* 楼主栏：头像/昵称可点击进入用户主页 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        {author || item.user_id ? (
          <Avatar
            style={{ cursor: 'pointer' }}
            src={proxiedImage(author?.head)}
            onClick={() => onOpenUser(author?.id ?? item.user_id!, author?.name ?? item.user_name)}
          >
            {(author?.name ?? item.user_name ?? '主')[0]}
          </Avatar>
        ) : null}
        <Typography.Text
          strong
          style={{ cursor: 'pointer' }}
          onClick={() => onOpenUser(author?.id ?? item.user_id!, author?.name ?? item.user_name)}
        >
          {author?.name ?? item.user_name ?? '匿名'}
        </Typography.Text>
        <Tag color="geekblue">{item.forum_name ?? item.source ?? '帖子'}</Tag>
        <Typography.Text type="secondary">{item.publish_time_str} · {item.click}</Typography.Text>
      </div>
      <Space size={8} wrap style={{ marginBottom: 8 }}>
        <Popconfirm title="点赞该帖子？" onConfirm={() => void doLike()}>
          <Button size="small" type="primary" ghost loading={liking} disabled={liked}>
            {liked ? '已点赞' : '点赞'}
          </Button>
        </Popconfirm>
        <Popconfirm
          title={`关注作者 ${author?.name ?? item.user_name ?? ''}？`}
          onConfirm={() => void doFollowAuthor()}
        >
          <Button size="small" loading={following} disabled={followed}>
            {followed ? '已关注作者' : '关注作者'}
          </Button>
        </Popconfirm>
      </Space>

      {/* 封面条仅在正文为空时兜底显示（正文里已含全部图片，避免重复） */}
      {!contentHtml && !contentLoading && (item.pics_arr?.length ?? 0) > 0 && (
        <Space wrap style={{ marginBottom: 12 }}>
          {item.pics_arr.slice(0, 4).map((p, i) => (
            <Image key={i} src={proxiedImage(p.url)} width={110} height={80} style={{ objectFit: 'cover' }} />
          ))}
        </Space>
      )}

      <Typography.Title level={5} style={{ marginTop: 8 }}>正文</Typography.Title>
      <Spin spinning={contentLoading}>
        {contentHtml ? (
          <div
            className="thread-content comment-content"
            style={{
              background: '#fafafa',
              padding: 12,
              borderRadius: 6,
              lineHeight: 1.8,
              wordBreak: 'break-word',
              minHeight: 48,
            }}
            dangerouslySetInnerHTML={{ __html: contentHtml }}
          />
        ) : (
          !contentLoading && (
            <Typography.Text type="secondary">
              无正文内容（可能为付费/隐藏帖或纯图帖）
            </Typography.Text>
          )
        )}
      </Spin>

      {reward.length > 0 && (
        <pre
          style={{ background: '#fafafa', padding: 8, borderRadius: 6, fontSize: 12, maxHeight: 160, overflow: 'auto' }}
        >
          {JSON.stringify(reward, null, 2)}
        </pre>
      )}

      {rewardRecords.length > 0 && (
        <div style={{ background: '#fffbe6', padding: 10, borderRadius: 6, marginBottom: 12 }}>
          <Typography.Text strong>打赏记录（{rewardRecords.length}）</Typography.Text>
          {rewardRecords.slice(0, 5).map((rr) => (
            <div key={`${rr.user_id}-${rr.reward_time_str}`} style={{ marginTop: 6, fontSize: 13 }}>
              <Typography.Text strong>{rr.name}</Typography.Text>
              <Typography.Text type="secondary"> · {rr.reward_time_str} · </Typography.Text>
              <Typography.Text type="warning">{rr.reward_value}</Typography.Text>
              {rr.remark && <Typography.Text type="secondary"> · {rr.remark}</Typography.Text>}
            </div>
          ))}
          {rewardRecords.length > 5 && (
            <Typography.Text type="secondary">…等 {rewardRecords.length} 条</Typography.Text>
          )}
        </div>
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        评论 {cTotal > 0 && <Typography.Text type="secondary">（{cTotal}）</Typography.Text>}
      </Typography.Title>

      {/* 发表评论（真实写操作，发出后所有人可见） */}
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input.TextArea
          value={commentText}
          onChange={(e) => setCommentText(e.target.value)}
          placeholder={item ? `评论「${(item.title ?? '').slice(0, 18)}…」（Enter 发送）` : '评论内容'}
          autoSize={{ minRows: 1, maxRows: 4 }}
          maxLength={1000}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              void doComment();
            }
          }}
        />
      </Space.Compact>
      <Button
        size="small"
        type="primary"
        style={{ marginBottom: 12 }}
        loading={commentSubmitting}
        disabled={!commentText.trim()}
        onClick={() => void doComment()}
      >
        发表评论
      </Button>

      <Spin spinning={cLoading}>
        {comments.length === 0 && !cLoading ? (
          <Empty description="暂无评论" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          comments.map((c) => (
            <div key={c.id} style={{ borderBottom: '1px solid #f0f0f0', padding: '10px 0' }}>
              <Space size={8}>
                <Avatar
                  size="small"
                  style={{ cursor: 'pointer' }}
                  src={proxiedImage(c.user_head)}
                  onClick={() => onOpenUser(c.user_id, c.user_name)}
                >
                  {(c.user_name ?? '?')[0]}
                </Avatar>
                <Typography.Text
                  strong
                  style={{ cursor: 'pointer' }}
                  onClick={() => onOpenUser(c.user_id, c.user_name)}
                >
                  {c.user_name}
                </Typography.Text>
                <Typography.Text type="secondary">#{c.id}</Typography.Text>
                {c.time && <Typography.Text type="secondary">{c.time}</Typography.Text>}
                {c.floor != null && <Typography.Text type="secondary">{c.floor}楼</Typography.Text>}
                {c.ip_address && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>IP {c.ip_address}</Typography.Text>
                )}
                <Typography.Text type="secondary">👍 {c.applaud_count}</Typography.Text>
              </Space>
              {/*
                评论为论坛富文本 HTML（含 <img>/<br>）。
                注意：内容来自社区用户，公网部署前需接入 DOMPurify 等做 XSS 消毒。
              */}
              <div
                className="comment-content"
                style={{ marginTop: 6, lineHeight: 1.7, wordBreak: 'break-word' }}
                dangerouslySetInnerHTML={{ __html: prepareCommentHtml(c.content) }}
              />
              {(c.pics?.length ?? 0) > 0 && (
                <Space wrap style={{ marginTop: 6 }}>
                  {c.pics.slice(0, 3).map((u, i) => (
                    <Image key={i} src={proxiedImage(u)} width={90} height={70} style={{ objectFit: 'cover' }} />
                  ))}
                </Space>
              )}
            </div>
          ))
        )}
      </Spin>

      {cTotal > C_STEP && (
        <Pagination
          style={{ marginTop: 12, textAlign: 'right' }}
          simple
          current={cPage}
          pageSize={C_STEP}
          total={cTotal}
          onChange={(p) => { setCPage(p); if (item) void loadComments(threadTid(item), p); }}
        />
      )}
    </Card>
  );
}
