import { describe, it, expect } from 'vitest';
import { searchItemToFeed, threadListItemToFeed, activityToFeed } from '../adapters';
import type {
  SearchThreadItem,
  ThreadListItem,
  UserActivityItem,
} from '../../types/sdgun-api-types';

/**
 * 三条映射的冒烟测试:只构造映射用得到的最小输入,断言输出 InfoFeedItem 的关键字段。
 * 输入用 `as` 收窄(上游条目字段繁多,测试不逐一补全),字段值刻意可辨以便定位。
 */

describe('searchItemToFeed', () => {
  const s = {
    tid: '9001',
    subject: '搜索标题',
    dateline: '5分钟前',
    pics: ['http://picapp.sdgun.net/a.jpg'],
    user_id: '11',
    user_name: '搜索作者',
    click: '123',
    link: '/mag/circle/v1/forum/threadViewPage?tid=9001',
  } as SearchThreadItem;

  it('映射关键字段', () => {
    const feed = searchItemToFeed(s);
    expect(feed.id).toBe(9001);
    expect(feed.title).toBe('搜索标题');
    expect(feed.source).toBe('搜索作者');
    expect(feed.from).toBe('搜索作者　123阅读');
    expect(feed.click).toBe('123阅读');
    expect(feed.publish_time_str).toBe('5分钟前');
    expect(feed.link).toBe('/mag/circle/v1/forum/threadViewPage?tid=9001');
    expect(feed.type).toBe(2);
    expect(feed.type_value).toBe('9001');
    expect(feed.pic_type).toBe(1);
    expect(feed.pics_count).toBe(1);
    expect(feed.pics_arr[0]).toEqual({ url: 'http://picapp.sdgun.net/a.jpg', tburl: 'http://picapp.sdgun.net/a.jpg', width: 0, height: 0 });
    // 类型缺口补齐:列表接口不带 reply_red_packet,经 feedItem() 断言补为 undefined
    expect(feed.reply_red_packet).toBeUndefined();
  });
});

describe('threadListItemToFeed', () => {
  const t = {
    tid: '7002',
    subject: '版块标题',
    dateline: '06-15',
    des: '摘要文本',
    user_id: '22',
    user_name: '版块作者',
    click: 45,
    pics: [{ url: 'http://pic.magcloud.cc/b.jpg', tburl: 'http://pic.magcloud.cc/tb.jpg' }],
    pics_count: 1,
  } as ThreadListItem;

  it('映射关键字段(link 由 tid 拼接)', () => {
    const feed = threadListItemToFeed(t);
    expect(feed.id).toBe(7002);
    expect(feed.title).toBe('版块标题');
    expect(feed.source).toBe('版块作者');
    expect(feed.from).toBe('版块作者　45阅读');
    expect(feed.click).toBe('45阅读');
    expect(feed.publish_time_str).toBe('06-15');
    expect(feed.link).toBe('/mag/circle/v1/forum/threadViewPage?tid=7002');
    expect(feed.des).toBe('摘要文本');
    expect(feed.type_value).toBe('7002');
    expect(feed.forum_name).toBeUndefined();
    expect(feed.pics_count).toBe(1);
    expect(feed.pics_arr[0]).toEqual({ url: 'http://pic.magcloud.cc/b.jpg', tburl: 'http://pic.magcloud.cc/tb.jpg', width: 0, height: 0 });
  });
});

describe('activityToFeed', () => {
  const a = {
    tid: '5003',
    title: '动态标题',
    pics_arr: [{ url: 'http://picapp.sdgun.net/c.jpg', tburl: 'http://picapp.sdgun.net/ctb.jpg', width: 10, height: 8 }],
    forum_name: '综合区',
    create_time: '1700000000',
    create_time_str: '8小时前',
    applaud_count: 7,
    is_applaud: false,
    click: '132阅读',
    link: '/mag/circle/v1/forum/threadViewPage?tid=5003',
    user: { id: '33', name: '动态作者', head: '' },
  } as UserActivityItem;

  it('映射关键字段(含作者与点赞数)', () => {
    const feed = activityToFeed(a);
    expect(feed.id).toBe(5003);
    expect(feed.title).toBe('动态标题');
    expect(feed.source).toBe('动态作者');
    expect(feed.from).toBe('动态作者　132阅读');
    expect(feed.click).toBe('132阅读');
    expect(feed.publish_time_str).toBe('8小时前');
    expect(feed.link).toBe('/mag/circle/v1/forum/threadViewPage?tid=5003');
    expect(feed.user_id).toBe('33');
    expect(feed.user_name).toBe('动态作者');
    expect(feed.forum_name).toBe('综合区');
    expect(feed.type_value).toBe('5003');
    expect(feed.applaud_count).toBe('7');
    expect(feed.pics_count).toBe(1);
  });
});
