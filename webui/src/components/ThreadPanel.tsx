import { useCallback, useEffect, useRef, useState } from 'react';
import { Avatar, Button, Card, Empty, Image, Input, Pagination, Popconfirm, Space, Spin, Tag, Typography, message } from 'antd';
import { pickData, ApiError } from '../api/client';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage, threadTid, extractThreadRow, enhanceVideos, renderRichText } from '../utils/media';
import { toastErr } from '../utils/toast';
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
  /** threadViewPage 返回了站方错误页（上游 502 等），正文确实取不到——与"本来就没有正文"区分开 */
  const [contentError, setContentError] = useState(false);
  /** 楼主信息（threadViewPage 的 var row 里有 user_id/user_name/user_head） */
  const [author, setAuthor] = useState<{ id: string; name: string; head: string } | null>(null);
  /** 发评论用的 circle_id（var row.circle_id） */
  const [circleId, setCircleId] = useState<string | number>('');
  /** 评论输入框 */
  const [commentText, setCommentText] = useState('');
  const [commentSubmitting, setCommentSubmitting] = useState(false);
  /** 定向回复目标(点某条评论的「回复」;null = 普通评论) */
  const [replyTarget, setReplyTarget] = useState<{ id: string; user_name: string } | null>(null);
  const [liked, setLiked] = useState(false);
  const [liking, setLiking] = useState(false);
  const [faved, setFaved] = useState(false);   // 本地收藏标记(sdgun_favorites)
  const [faving, setFaving] = useState(false);
  const [followed, setFollowed] = useState(false);
  const [following, setFollowing] = useState(false);
  /** 正文容器引用:渲染后对 <video> 做尺寸自适应与 HEVC 提示(enhanceVideos) */
  const contentRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (contentHtml && contentRef.current) enhanceVideos(contentRef.current);
  }, [contentHtml]);

  const loadComments = useCallback(async (tid: number, p: number) => {
    setCLoading(true);
    try {
      const resp = await sdgunApi.comments(tid, p, C_STEP);
      setComments(resp.list ?? []);
      setCTotal(resp.count ?? 0);
    } catch (e) {
      setComments([]);
      const ae = e as ApiError;
      // 上游对「tid 不存在/不可见」统一报 code=502「您的网络有些问题」（对照实测：
      // 从不存在的 tid/远古空洞与被删帖错误签名完全一致），文案极具误导性，此处纠偏
      const reason = ae.code === 502 ? '帖子不存在或已不可见（可能刚被删除或审核中），并非网络问题' : ae.message;
      message.error(`评论加载失败：${reason}`);
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
    setFaved(false);
    setContentHtml('');
    setContentError(false);
    setAuthor(null);
    setCircleId('');
    setCommentText('');
    setReplyTarget(null);   // 换帖后不能沿用上一篇的回复目标
    if (item) {
      const tid = threadTid(item);
      // 本地收藏状态(毫秒级接口,失败静默按未收藏处理)
      sdgunApi.favoritesHas(tid).then((r) => setFaved(Boolean(r.has))).catch(() => {});
      void loadComments(tid, 1);
      setContentLoading(true);
      sdgunApi
        .fetchThreadHtml(tid)
        .then((html) => {
          // 站方错误页也是 200 + HTML（含 error 样式表/「您的网络有些问题」文案），
          // 必须在这里识别，否则会被当成"没有正文"静默吞掉
          if (html.includes('css/user/error-') || html.includes('您的网络有些问题')) {
            setContentError(true);
            return;
          }
          const row: ThreadRowData | null = extractThreadRow(html);
          setContentHtml(renderRichText(row?.content ?? ''));
          if (row?.user_id) {
            setAuthor({
              id: String(row.user_id),
              name: String(row.user_name ?? item.user_name ?? '楼主'),
              head: String(row.user_head ?? ''),
            });
          }
          if (row?.circle_id != null) setCircleId(row.circle_id as string | number);
        })
        .catch((e) => toastErr('正文加载失败', e))
        .finally(() => setContentLoading(false));
    }
  }, [item, loadComments]);

  /** 发表评论/定向回复(成功后回到第 1 页刷新,新评论在最前) */
  const doComment = async () => {
    if (!item || !commentText.trim()) return;
    setCommentSubmitting(true);
    try {
      // replyTarget 非空时带上 reply_id:上游把回复与主评论平铺在同一个 list 里,
      // 回复项会带 to_comment 引用(见 API-ANALYSIS 附录 12)
      const resp = await sdgunApi.addComment(threadTid(item), commentText.trim(), circleId,
                                            replyTarget?.id);
      message.success(resp.data?.message_result === 'post_reply_succeed' ? '评论发表成功' : '评论已提交');
      setCommentText('');
      setReplyTarget(null);
      setCPage(1);
      void loadComments(threadTid(item), 1);
    } catch (e) {
      toastErr('评论失败', e);
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
      toastErr('红包查询失败', e);
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
      // 本地归档(上游无"我的点赞"接口;失败静默,不影响点赞本身)
      sdgunApi.likesAdd(threadTid(item), item.title ?? '').catch(() => {});
    } catch (e) {
      toastErr('点赞失败', e);
    } finally {
      setLiking(false);
    }
  };

  /** 收藏/取消收藏(本地标记;上游收藏接口未逆向,见 sdgun_favorites.py) */
  const doFavorite = async () => {
    if (!item) return;
    const tid = threadTid(item);
    setFaving(true);
    try {
      if (faved) {
        await sdgunApi.favoritesRemove(tid);
        setFaved(false);
        message.success('已取消收藏');
      } else {
        await sdgunApi.favoritesAdd(tid, item.title ?? '');
        setFaved(true);
        message.success('已收藏（在我的主页「收藏」tab 查看）');
      }
    } catch (e) {
      toastErr('收藏操作失败', e);
    } finally {
      setFaving(false);
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
      toastErr('关注失败', e);
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
      toastErr('打赏查询失败', e);
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
      title={`帖子详情 · tid=${threadTid(item)}`}
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
        <Button
          size="small"
          type={faved ? 'primary' : 'default'}
          ghost={faved}
          loading={faving}
          onClick={() => void doFavorite()}
        >
          {faved ? '★ 已收藏' : '☆ 收藏'}
        </Button>
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
            ref={contentRef}
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
          !contentLoading &&
          (contentError ? (
            <Typography.Text type="warning">
              该帖已不可访问（可能发表后即被删除、审核中或不存在）；标题/作者来自列表缓存仍可见
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">
              无正文内容（可能为付费/隐藏帖或纯图帖）
            </Typography.Text>
          ))
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
      {replyTarget && (
        <div style={{ marginBottom: 6 }}>
          <Tag color="blue" closable onClose={() => setReplyTarget(null)}>
            回复 @{replyTarget.user_name}
          </Tag>
        </div>
      )}
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input.TextArea
          value={commentText}
          onChange={(e) => setCommentText(e.target.value)}
          placeholder={replyTarget
            ? `回复 @${replyTarget.user_name}`
            : (item ? `评论「${(item.title ?? '').slice(0, 18)}…」` : '评论内容')}
          autoSize={{ minRows: 1, maxRows: 4 }}
          maxLength={1000}
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
                <Button
                  size="small"
                  type={replyTarget?.id === c.id ? 'primary' : 'text'}
                  onClick={() => setReplyTarget({ id: c.id, user_name: c.user_name })}
                >
                  回复
                </Button>
              </Space>
              {/* 楼中楼的引用块:被回复评论的原文(@昵称 + 消毒后的富文本),样式仿 WAP comment-feed */}
              {c.to_comment?.id && (
                <div
                  style={{
                    marginTop: 6,
                    padding: '6px 10px',
                    background: '#f5f5f5',
                    borderLeft: '3px solid #d9d9d9',
                    borderRadius: 4,
                    fontSize: 12,
                    lineHeight: 1.6,
                    wordBreak: 'break-word',
                  }}
                >
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    @{c.to_comment.user_name}
                  </Typography.Text>
                  {/*
                    引用内容同样来自社区用户:renderRichText 内部已是
                    prepareCommentHtml → sanitizeHtml(DOMPurify),不能直出。
                  */}
                  <div
                    className="comment-content"
                    style={{ marginTop: 2 }}
                    dangerouslySetInnerHTML={{ __html: renderRichText(c.to_comment.content ?? '') }}
                  />
                </div>
              )}
              {/*
                评论为论坛富文本 HTML（含 <img>/<br>），renderRichText 已在改写后
                过 DOMPurify 消毒——内容来自社区用户，这道闸在任何部署形态都不能省。
              */}
              <div
                className="comment-content"
                style={{ marginTop: 6, lineHeight: 1.7, wordBreak: 'break-word' }}
                dangerouslySetInnerHTML={{ __html: renderRichText(c.content) }}
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
