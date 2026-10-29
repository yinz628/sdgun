import { api, getJson, postForm } from './client';
import type {
  ChannelConfigResponse,
  ChatMsgItem,
  ChatUnreadMsgResponse,
  CommentListResponse,
  ConversationsResponse,
  DayReadFinishResponse,
  FavoritesListResponse,
  GetRewardContentResponse,
  IndexPagesResponse,
  InfoListResponse,
  JsApiResponse,
  LikesListResponse,
  LoadUsersResponse,
  MyFollowsListResponse,
  NotifyIndexResponse,
  NotifyListResponse,
  RepairTokenResponse,
  ReplyAddResponse,
  RewardRecordResponse,
  SendChatMsgResponse,
  CircleTreeResponse,
  ThreadListItem,
  ThreadListResponse,
  ThreadRowData,
  ThreadSearchResponse,
  ToolsDefaultGetFourResponse,
  UserActivityResponse,
  UserDetailResponse,
  UserHomeResponse,
  UserSearchResponse,
  ContentAddApplaudResponse,
  FansAddResponse,
  WatchHitsResponse,
  WatchRule,
  WatchRulesResponse,
  WatchSettingsResponse,
  WatchStatus,
} from '../types/sdgun-api-types';

/** 与抓包/代理对齐的接口封装（fid=0 已实测等价于真实 fid） */
export const sdgunApi = {
  /** 当前登录用户（jsApi，含主站 token） */
  currentUser: () => getJson<JsApiResponse>('/mag/index/v1/Index/jsApi'),

  /** 首页框架：栏目 Tab（index_fragment 是 JSON 字符串，需二次 parse） */
  indexPages: () => getJson<IndexPagesResponse>('/mag/index/v2/index/pages'),

  /** 频道配置：含频道名与分类列表 data.info_config[] */
  channelConfig: (channelId: number) =>
    getJson<ChannelConfigResponse>('/mag/info/v3/channel/channelConfigById', {
      channel_id: channelId,
    }),

  /** 版块树（分组 → 各版块 fid/名称），版块浏览的导航源 */
  circleTree: () => getJson<CircleTreeResponse>('/mag/circle/v1/Circle/circleTree'),

  /** 版块帖子列表（真实分页；fid 来自 circleTree 的 type_value） */
  forumThreads: (fid: number, p = 1, step = 20) =>
    getJson<ThreadListResponse>('/mag/circle/v1/Forum/threadList', { fid, p, step }),

  /** 分类信息流 v3（cat 类栏目：今日热议/本周最热/公告等，支持分页） */
  infoListV3: (catId: number, p = 1, step = 20) =>
    getJson<InfoListResponse>('/mag/info/v3/info/infoListByCatId', {
      cat_id: catId,
      is_insert_ad: -1,
      p,
      step,
    }),

  /** 帖子搜索（注意参数名是复数 keywords；title 字段带高亮 HTML） */
  searchThreads: (keywords: string, p = 1, step = 20) =>
    getJson<ThreadSearchResponse>('/mag/circle/v1/Forum/threadSearch', {
      keywords,
      p,
      step,
    }),

  /** 用户搜索（注意参数是 name 单数；user_title 字段带高亮 HTML） */
  searchUsers: (name: string) =>
    getJson<UserSearchResponse>('/mag/user/v1/User/userSearch', { name }),

  /** 默认工具 + 热门搜索词（搜索页联想词） */
  defaultTools: () =>
    getJson<ToolsDefaultGetFourResponse>('/mag/config/v1/Tools/toolsDefaultGetFour'),

  /** 帖子点赞（content_id = tid；code=101 表示已点过） */
  applaudContent: (contentId: string | number) =>
    postForm<ContentAddApplaudResponse>('/mag/circle/v1/Forum/contentAddApplaud', {
      content_id: contentId,
    }),

  /** 关注用户（code=101 表示已关注） */
  followUser: (userId: string | number) =>
    postForm<FansAddResponse>('/mag/user/v1/UserRelation/fansAdd', { user_id: userId }),

  /** 我的关注列表（user_id=自己；p/step 分页，无 total——条数<step 即末页） */
  myFollowsList: (meUid: string | number, p = 1, step = 20) =>
    getJson<MyFollowsListResponse>('/mag/user/v1/UserNotify/MyFollowsList', {
      user_id: meUid, p, step, lat: 0, lng: 0,
    }),

  /** 点赞成功后上报本地归档（tid+标题；幂等）与查询历史（上游无"我的点赞"接口） */
  likesAdd: (tid: number, title: string) =>
    api.post('/likes/add', { tid, title }).then((r) => r.data as { added: boolean; total: number }),
  likesList: (limit = 200) =>
    getJson<LikesListResponse>('/likes/list', { limit }),

  /** 帖子收藏（本地标记，可增删；上游收藏接口未逆向） */
  favoritesAdd: (tid: number, title: string) =>
    api.post('/favorites/add', { tid, title }).then((r) => r.data as { added: boolean; total: number }),
  favoritesRemove: (tid: number) =>
    api.post('/favorites/remove', { tid, title: '' }).then((r) => r.data as { removed: boolean; total: number }),
  favoritesHas: (tid: number) =>
    getJson<{ has: boolean }>('/favorites/has', { tid }),
  favoritesList: (limit = 500) =>
    getJson<FavoritesListResponse>('/favorites/list', { limit }),

  /* ===== 关注关键字监控（本地代理，代理侧守护线程轮询信息流） ===== */

  /** 监控状态：开关/间隔/监控源/规则与命中计数/最近一轮结果 */
  watchStatus: () => getJson<WatchStatus>('/watch/status'),

  /** 命中记录：since<0 取最新 limit 条；since>=0 只取更新的（前端游标） */
  watchHits: (since = -1, limit = 100) =>
    getJson<WatchHitsResponse>('/watch/hits', { since, limit }),

  /** 规则列表（抽屉打开时拉取；与 watchStatus/watchHits 并发） */
  watchRules: () => getJson<WatchRulesResponse>('/watch/rules'),

  /** 新增规则：keywords 用逗号/顿号/空格分隔（同一规则内需全部命中） */
  watchRulesAdd: (keywords: string, name = '') =>
    api.post('/watch/rules', { keywords, name }).then((r) => r.data as WatchRulesResponse & { rule: WatchRule }),
  watchRulesRemove: (id: number) =>
    api.post('/watch/rules/remove', { id }).then((r) => r.data as WatchRulesResponse & { removed: boolean }),
  watchRulesToggle: (id: number) =>
    api.post('/watch/rules/toggle', { id }).then((r) => r.data as WatchRulesResponse & { rule: WatchRule | null }),

  /** 立即检测一次（代理侧同步执行一轮轮询） */
  watchRun: () =>
    api.post('/watch/run').then((r) => r.data as { ok: boolean; new_items: number; hits_added: number; error: string | null }),

  /** 监控设置（目前只有提醒时间窗 window_hours: 12/24/48） */
  watchSettings: () => getJson<WatchSettingsResponse>('/watch/settings'),
  watchSettingsSet: (windowHours: number) =>
    api.post('/watch/settings', { window_hours: windowHours })
      .then((r) => r.data as WatchSettingsResponse),

  /** 帖子正文页 HTML（正文在内嵌 var row = {...}.content，用 extractThreadRow 提取） */
  fetchThreadHtml: (tid: number | string) =>
    api
      .get<string>('/mag/circle/v1/forum/threadViewPage', {
        params: { tid, mag_hide_progress: 1, themecolor: '111111' },
        responseType: 'text',
      })
      .then((r) => r.data as string),

  /** 帖子打赏记录 */
  rewardRecord: (tid: string | number) =>
    getJson<RewardRecordResponse>('/mag/user/v1/reward/listRewardRecord', {
      type: 'forum',
      type_value: tid,
    }),

  /** 频道信息流（真实分页：p=1 响应 totalList 为游标，p>=2 必须传 contentIds=totalList 切片） */
  feed: (params: {
    channel_id: number;
    cat_id: number;
    p: number;
    step: number;
    contentIds?: number[];
  }) =>
    getJson<InfoListResponse>('/mag/info/v2/channel/infoListByCatId', {
      channel_id: params.channel_id,
      cat_id: params.cat_id,
      p: params.p,
      step: params.step,
      tab: 0,
      is_app_first: -1,
      content_id: params.contentIds?.length ? params.contentIds.join(',') : undefined,
    }),

  /**
   * 帖子评论（分页）。v1 与 v2 参数/响应逐字段一致（2026-09-07 对照实测：
   * 热帖 count/列表/分页/排序完全相同），作为 v2 失败时的防御性回落。
   * 注意：上游把「tid 不存在/不可见」统一报成 code=502「您的网络有些问题」，
   * 两版接口同错——回落只用于抵御单一接口版本波动，不改变错误语义。
   */
  comments: async (tid: number, p = 1, step = 10): Promise<CommentListResponse> => {
    const params = { tid, fid: 0, order: 2, p, step, authorid: 0 };
    try {
      return await getJson<CommentListResponse>('/mag/circle/v2/Forum/commentList', params);
    } catch (e) {
      return getJson<CommentListResponse>('/mag/circle/v1/Forum/commentList', params);
    }
  },

  /**
   * 发表评论 / 回复某条评论（circle_id 来自 threadViewPage 的 var row；成功后 data 即新评论）。
   * 定向回复传 replyId（目标评论 id，WAP replyCmtItem 的 reply_id=o.id），表单才带 reply_id；
   * 普通评论不带该字段。
   */
  addComment: (tid: number | string, content: string, circleId?: string | number,
               replyId?: string | number) => {
    const form: Record<string, string | number> = {
      source: 'qiniu',
      from_push: '',
      tid,
      circle_id: circleId ?? '',
      content,
      auth: '',
      video: '',
      vest_id: '',
    };
    if (replyId != null && replyId !== '') form.reply_id = replyId;
    return postForm<ReplyAddResponse>('/mag/circle/v1/Forum/replyAdd', form);
  },

  /** 帖子悬赏/红包内容 */
  reward: (tid: number) =>
    postForm<GetRewardContentResponse>('/mag/postreward/v1/ReleaseReward/getRewardContent', {
      type_value: tid,
      type: 1,
    }),

  /** 每日阅读任务上报 */
  dayReadFinish: () =>
    postForm<DayReadFinishResponse>('/mag/user/v2/user/dayReadFinish', {
      type: 'readContent',
    }),

  /** 换取聊天服务 token */
  repairChatToken: (userId: string | number) =>
    postForm<RepairTokenResponse>('/mag/user/v1/User/repairChatAndResToken', {
      user_id: userId,
    }),

  /** 聊天未读（走代理 /msg/**，解决 http 明文混合内容问题） */
  chatUnread: (chatToken: string, userId: string | number) =>
    getJson<ChatUnreadMsgResponse>('/msg/unReadMsg', {
      app_key: 'shuidan',
      step: 100,
      token: chatToken,
      user_id: `user${userId}`,
    }),

  /**
   * 本地归档的私信历史（代理 /messages/local，自归档功能部署起累积）。
   * 上游无历史接口且 unReadMsg 拉取即清（附录 8），归档是网页端唯一的历史来源。
   */
  localMessages: (peerId?: string, limit = 500) =>
    getJson<{ msgs: ChatMsgItem[]; total: number }>('/messages/local', {
      peer_id: peerId,
      limit,
    }),

  /** 本地归档的会话列表（按对端分组摘要，me=user<uid>），私信入口用 */
  localConversations: (meUid: string | number) =>
    getJson<ConversationsResponse>('/messages/conversations', { me: `user${meUid}` }),

  /** 通知中心索引：各分类（评论/@/赞/关注/私信）未读计数与最新一条预览 */
  notifyIndex: () => getJson<NotifyIndexResponse>('/mag/user/v3/userNotify/userNotifyIndex'),

  /** 通知明细（code=comment/at/applaud/focus；条目 link 可跳回帖子） */
  notifyList: (code: string) =>
    getJson<NotifyListResponse>('/mag/user/v3/UserNotify/notifyList', { code }),

  /* ===== 用户主页（第四份抓包 sdgun-2026-08-25） ===== */

  /** 用户主页框架（name 为 RSA 密文，明文昵称用 loadUsers） */
  userHome: (userId: string | number) =>
    getJson<UserHomeResponse>('/mag/user/v5/user/userHome', { user_id: userId }),

  /** 明文用户信息（昵称/签名/是否可私信） */
  loadUsers: (userIds: Array<string | number>) =>
    getJson<LoadUsersResponse>('/mag/user/v1/User/loadUsers', {
      user_ids: userIds.join(','),
    }),

  /** 主页「资料」tab：注册时间/用户组/积分等 + 兴趣标签 */
  userDetail: (userId: string | number) =>
    getJson<UserDetailResponse>('/mag/user/v3/user/getUserInfo', { user_id: userId }),

  /** 主页「动态」tab（发过的帖子和动态，含点赞/评论） */
  userActivity: (userId: string | number, p = 1, step = 20) =>
    getJson<UserActivityResponse>('/mag/user/v2/User/userActivityPage', {
      user_id: userId,
      p,
      step,
    }),

  /** 主页「论坛」tab（config_id=3）/「圈子」tab（config_id=4） */
  userForumOrShow: (userId: string | number, configId: 3 | 4, p = 1, step = 20) =>
    getJson<UserActivityResponse>('/mag/user/v2/user/userForumOrShowPage', {
      user_id: userId,
      config_id: configId,
      style_type: configId === 4 ? 'pintu' : undefined,
      p,
      step,
    }),

  /* ===== 私信收发（第六份抓包 sdgun-2026-08-25-2） ===== */

  /**
   * 发送私信（走代理 /msg/**）。
   * sign/sign2 必须存在但实测不校验内容（固定 32 个 0 可过，缺失报「消息签名错误」）。
   * 注意上游偶发 >20s 慢响应：超时不代表发送失败，发送后应刷新未读确认。
   */
  sendChatMsg: (params: {
    chatToken: string;
    meId: string | number;
    toUserId: string | number;
    text: string;
    myName?: string;
    myHead?: string;
  }) => {
    const now = Date.now();
    const form: Record<string, string | number> = {
      app_key: 'shuidan',
      'data[body_type]': '1',
      'data[content]': JSON.stringify({ text: params.text }),
      'data[method]': 'sendMsgToUser',
      'data[msg_id]': `${now / 1000}`,
      'data[time_stamp]': `${now / 1000}`,
      'data[to_user_id]': `user${params.toUserId}`,
      sign: '0'.repeat(32),
      sign2: '0'.repeat(32),
      timestamp: Math.floor(now / 1000),
      token: params.chatToken,
      type: 'user',
      user_id: `user${params.meId}`,
    };
    if (params.myName) {
      // ext 为发送者昵称/头像，可选（实测不带也能发送）
      form['data[ext]'] = JSON.stringify({
        user_name: params.myName,
        user_head: params.myHead ?? '',
      });
    }
    // 上游偶发 >20s 慢响应(见函数头注释),单独放宽超时,减少"超时但实际已发出"的误报
    return postForm<SendChatMsgResponse>('/msg/sendMsg', form, { timeout: 45000 });
  },
};
