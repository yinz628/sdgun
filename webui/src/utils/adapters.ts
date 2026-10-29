/**
 * 列表条目 → 信息流条目(InfoFeedItem)适配器。
 *
 * searchThreads / forumThreads / userActivity 三个列表接口返回的条目结构各不相同,
 * 但右侧详情面板(ThreadPanel)统一消费 InfoFeedItem,故集中在此做映射
 * (原散落在 FeedList.tsx 与 UserPanel.tsx,2026-10-29 收敛到本文件:纯搬家,
 * 字段与顺序均未改动)。
 *
 * 类型缺口:InfoFeedItem 的 reply_red_packet 为必填(上游信息流接口会返回),
 * 而这三个列表接口的条目都不带该字段,详情面板也从不读它。因此统一经 feedItem()
 * 补齐为 InfoFeedItem——只有这一处断言,不再每个函数各写一次 `as unknown as`。
 */

import type {
  InfoFeedItem,
  SearchThreadItem,
  ThreadListItem,
  UserActivityItem,
} from '../types/sdgun-api-types';

/** InfoFeedItem 去掉列表接口不提供的必填字段(仅 reply_red_packet)后的草稿类型 */
type FeedItemDraft = Omit<InfoFeedItem, 'reply_red_packet'>;

/** 把列表条目的映射结果补成 InfoFeedItem(见文件头「类型缺口」说明) */
function feedItem(draft: FeedItemDraft): InfoFeedItem {
  return draft as InfoFeedItem;
}

/** 搜索条目 → 信息流条目（右侧详情面板复用同一结构；threadTid 走 link 解析） */
export function searchItemToFeed(s: SearchThreadItem): InfoFeedItem {
  return feedItem({
    id: Number(s.tid),
    title: s.subject,
    pic_type: s.pics?.length ? 1 : 0,
    pics_arr: (s.pics ?? []).map((u) => ({ url: u, tburl: u, width: 0, height: 0 })),
    from: `${s.user_name}　${s.click}阅读`,
    source: s.user_name,
    click: `${s.click}阅读`,
    publish_time_str: s.dateline,
    label: '',
    label_id: null,
    link: s.link,
    type: 2,
    type_value: s.tid,
    user_id: s.user_id,
    user_name: s.user_name,
    forum_name: s.forum_name,
    des: '',
    tags: [],
    video_url: null,
    is_vote: -1,
    goods_info: '',
    is_voice: -1,
    is_live: null,
    pics_count: s.pics?.length ?? 0,
    cover_type: 1,
  });
}

/** 版块帖子 → 信息流条目（右侧详情面板复用同一结构） */
export function threadListItemToFeed(t: ThreadListItem): InfoFeedItem {
  return feedItem({
    id: Number(t.tid),
    title: t.subject,
    pic_type: (t.pics?.length ?? 0) > 0 ? 1 : 0,
    pics_arr: (t.pics ?? []).map((p) => ({ url: p.url, tburl: p.tburl, width: 0, height: 0 })),
    from: `${t.user_name}　${t.click}阅读`,
    source: t.user_name,
    click: `${t.click}阅读`,
    publish_time_str: t.dateline,
    label: '',
    label_id: null,
    link: `/mag/circle/v1/forum/threadViewPage?tid=${t.tid}`,
    type: 2,
    type_value: t.tid,
    user_id: t.user_id,
    user_name: t.user_name,
    forum_name: undefined,
    des: t.des || '',
    tags: [],
    video_url: null,
    is_vote: -1,
    goods_info: '',
    is_voice: -1,
    is_live: null,
    pics_count: t.pics_count ?? (t.pics?.length ?? 0),
    cover_type: 1,
  });
}

/** 用户动态/帖子条目 → 信息流条目（复用 ThreadPanel 的结构） */
export function activityToFeed(a: UserActivityItem): InfoFeedItem {
  return feedItem({
    id: Number(a.tid),
    title: a.title ?? '(无标题)',
    pic_type: (a.pics_arr?.length ?? 0) > 0 ? 1 : 0,
    pics_arr: a.pics_arr ?? [],
    from: `${a.user?.name ?? ''}　${a.click}`,
    source: a.user?.name ?? '',
    click: a.click,
    publish_time_str: a.create_time_str,
    label: '',
    label_id: null,
    link: a.link ?? `/mag/circle/v1/forum/threadViewPage?tid=${a.tid}`,
    type: 2,
    type_value: a.tid,
    user_id: a.user?.id ?? '',
    user_name: a.user?.name,
    forum_name: a.forum_name,
    des: '',
    tags: [],
    video_url: null,
    is_vote: -1,
    goods_info: '',
    is_voice: -1,
    is_live: null,
    pics_count: a.pics_arr?.length ?? 0,
    cover_type: 1,
    applaud_count: String(a.applaud_count ?? 0),
  });
}
