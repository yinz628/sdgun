/**
 * SDGun「水弹社区」App API 类型定义
 * 依据 sdgun-2026-08-24.json 抓包逆向，字段以实际样本为准；
 * 样本未覆盖到的分支以注释标注，宽松字段用 [key: string]: unknown 兜底。
 */

/* ============ 通用包裹 ============ */

/** 统一响应包裹：code === 100 成功，101 = 已完成/重复操作等语义 */
export interface ApiEnvelope {
  success: boolean;
  code: number;
  msg: string | null;
}

/** 列表型响应：数据挂在顶层 list（不在 data 内） */
export interface ApiListResponse<T> extends ApiEnvelope {
  list: T[];
}

/** data 型响应 */
export interface ApiResponse<T> extends ApiEnvelope {
  data: T;
}

/* ============ 通用子结构 ============ */

export interface PicItem {
  aid?: string;
  url: string;
  tburl: string; // 缩略图（常与 url 相同）
  width: number;
  height: number;
  video_url?: string;
}

/** 回帖红包展示配置（几乎所有内容实体都内嵌一份） */
export interface ReplyRedPacket {
  is_open_reply_red_packet: number; // -1 关闭
  has_reply_red_packet: number;
  comment_prompt: string;
  link: string;
  detail_cover_id_url: string;
  big_cover_id_url: string;
  des: string;
  start_time: number;
  unstart_prompt_word: string;
  unreply_prompt_word: string;
  wap_guide_prompt_word: string;
  audit_prompt_word: string;
  red_type: number;
  comment_red_tag_link: string;
}

/** 帖子/内容红包角标 */
export interface RedPacketBadge {
  has_red_packet: number;
  red_packet_hover_pic: string;
  detail_red_des: string;
  red_label: string;
}

export interface UserBrief {
  head?: string; // 头像
  user_id?: string | number;
  user_name?: string;
  user_sex?: string;
}

/* ============ 1. repairChatAndResToken ============ */

export interface RepairTokenRequest {
  user_id: string; // form: user_id=683237
}

export interface RepairTokenResponse extends ApiEnvelope {
  data: {
    chatToken: string; // 聊天服务专用 token
  };
}

/* ============ 2. index/pages 首页框架 ============ */

export interface IndexPagesRequest {
  find_key?: string; // 客户端缓存的版本键，未缓存可省
  fragment_key?: string;
}

/** 频道 Tab（index_fragment 内的 JSON 字符串，需二次 JSON.parse） */
export interface IndexFragmentItem {
  name: string; // Tab 名，如 "聚焦"
  link: string; // shuidan://channelList?channel_id=6 原生跳转协议
  id: string;
  type?: number; // 1=频道 2=信息流分类
  column_type: number;
  source: string;
  uniqid: string; // 频道唯一标识，后续请求回传
  fixed: number;
  type_name: string;
  is_default?: number;
}

export interface IndexPagesResponse extends ApiEnvelope {
  find_key: string;
  fragment_key: string;
  /** 注意：是 JSON 字符串，需再 parse 成 IndexFragmentItem[] */
  index_fragment: string;
  /** 同上，发现页组件配置的 JSON 字符串 */
  find_page: string;
}

/* ============ 3. index/config 全局配置 ============ */

export interface PostConfigItem {
  circle_id: string;
  name: string; // 版块名，如 "综合讨论"
  des: string;
  pic: string;
  link: string; // shuidan:// 原生发帖协议
  fid: string;
  icon_url: string;
}

export interface IndexConfigResponse extends ApiEnvelope {
  data: {
    seek_exp: string;
    res_key: string;
    signin: string; // 签到页路由
    usertask: string;
    mall: string;
    seek_url: string; // 站内搜索
    invite: string;
    global_user_registered: string; // "1" 注册开放
    global_user_registered_close_msg: string;
    global_is_user_login: string;
    global_is_code_login: string; // "1" 验证码登录可用
    global_is_weixin_login: number;
    global_is_qq_login: number;
    domain_cooperation: string[];
    domain_white: string[]; // 外链白名单
    default_show: { circleId: string | null; name: string | null };
    default_forum: { circleId: string | null; name: string | null; fid: string | null };
    tab_content: { circle_id: string; circle_name: string; wap_url: string };
    post_config: PostConfigItem[]; // 发帖版块
    [key: string]: unknown;
  };
}

/* ============ 4/5. 信息流列表 ============ */

export interface InfoListByCatIdRequest {
  cat_id: number | string;
  channel_id?: number | string; // v2 频道流必带
  p?: number; // 页码，默认 1
  step?: number; // 每页条数，默认 20
  tab?: number;
  is_app_first?: number; // -1
  uniqid?: string; // 频道 uniqid
  is_insert_ad?: number; // v3：是否混入广告位
  /**
   * v2 频道流的翻页游标（第四份抓包确认）：p=1 响应带 totalList（全量有序 id 数组），
   * p>=2 时必须带 content_id=totalList[(p-1)*step : p*step]（逗号分隔），
   * 服务端按这批 id 批量取详情；不带 content_id 的 p=2 返回空列表。
   */
  content_id?: string;
}

/** 信息流条目（v2/v3 字段并集） */
export interface InfoFeedItem {
  id: number; // 帖子 tid
  title: string;
  pic_type: number; // 1 有图
  pics_arr: PicItem[];
  pics_count: number;
  from: string; // "来源 N阅读"
  source: string;
  click: string; // "2.1万阅读"
  publish_time?: string; // unix 秒（字符串）
  publish_time_str: string; // "1小时前"
  label: string;
  label_id: string | null;
  link: string; // 帖子详情相对路径
  wap_link?: string; // WAP 详情绝对地址
  type: number; // 2=普通帖 6=公告 等
  type_value: string; // 帖子 tid
  user_id: string | number;
  user_name?: string;
  user?: UserBrief;
  forum_name?: string; // 所属版块
  des: string; // 摘要
  applaud_count?: string; // 点赞数
  comment_count?: number;
  is_applaud?: boolean;
  cover_type: string | number;
  cover_set?: number;
  tags: string[] | unknown[];
  red_packet?: RedPacketBadge;
  redPacket?: unknown | null;
  reply_red_packet: ReplyRedPacket;
  video_url: string | null;
  is_vote: number;
  goods_info: string;
  is_voice: number;
  is_live: number | null;
  share_info?: unknown;
  highlight_publisher?: string;
  [key: string]: unknown;
}

export interface InfoListResponse extends ApiListResponse<InfoFeedItem> {
  cat_name?: string; // v3 返回，如 "公告"
  cover_set?: number;
  style?: string; // "info"
  show_voice?: boolean;
  highlight_publisher?: string;
  /** v2 频道流 p=1 返回：全量有序内容 id（作为翻页游标，共 500 条档位） */
  totalList?: number[];
}

/* ============ 6. channelConfigById 频道组件 ============ */

/** 频道下的信息流分类（data.info_config[]） */
export interface ChannelCatItem {
  link: string; // shuidan://infolist?cat_id=N&channel_id=M
  cat_id: number;
  type: number;
  id: number;
  cat_name: string; // 如 "今日新帖" / "玩具交易"
  cat_pic: string;
  cat_pic_not: string;
  style: string;
}

export interface OperativeComponent {
  _name: string; // 组件名 "图片轮播"
  _type: string; // pic_slide | user_tool | banner_component | rolling_notice ...
  show: boolean;
  comp_type: string; // static
  name: string;
  place?: string; // 广告位标识 → ad/listNotEndByPlace 的 place 参数
  items?: Array<{
    picId: string;
    link: string;
    pic: string;
    um_event?: UmEvent;
  }>;
  [key: string]: unknown;
}

export interface UmEvent {
  um_click_event_id?: string;
  um_show_event_id?: string;
  um_event_data?: Record<string, unknown>;
}

export interface ChannelConfigResponse extends ApiEnvelope {
  data: {
    channel_name?: string;
    info_config?: ChannelCatItem[];
    operative_config: OperativeComponent[];
  };
}

/* ============ 7. toolsAllGet 工具集 ============ */

export interface ToolItem {
  picId: string;
  link: string; // http(s):// 或 shuidan:// 原生协议
  title: string;
  marking: string;
  keywords: string;
  pic: string;
}

export interface ToolsAllGetResponse extends ApiEnvelope {
  data: {
    all: Array<{ items: ToolItem[] }>;
  };
}

/* ============ 8. dayReadFinish 每日阅读 ============ */

export interface DayReadFinishRequest {
  type: 'readContent';
}

export interface DayReadFinishResponse extends ApiEnvelope {
  is_finish: boolean; // code=101 时为 true（今日已完成）
}

/* ============ 9. userNotifyIndex 通知中心 ============ */

export interface NotifyCountItem {
  code: 'at' | 'focus' | 'applaud' | 'comment' | 'chat';
  count: number;
  content: string;
  create_time: string;
  create_time_str: string;
}

export interface AssistantMenuItem {
  name: string;
  link: string;
  child: AssistantMenuItem[];
}

export interface Assistant {
  id: number;
  name: string; // "SDGun版务组" | "群通知" | "激励中心"
  pic_url: string;
  type: number;
  menu: AssistantMenuItem[];
}

export interface UserNotifyIndexResponse extends ApiEnvelope {
  data: {
    open_letter: number;
    has_letter: boolean;
    open_push: number;
    user_special_relation: number;
    notifyList: NotifyCountItem[];
    assistantList: Assistant[];
  };
}

/* ============ 10. ad/listNotEndByPlace 广告 ============ */

export interface AdListRequest {
  place: string; // 广告位，如 pic_slide_1559697851
}

export interface AdItem {
  id: number;
  title: string;
  pic: string; // 附件 id，真实图看 pic_arr
  begin_time: number;
  end_time: number;
  link: string;
  place: string;
  position: number;
  close: number; // 1 可关闭
  pic_theme_color: string;
  column_type: number;
  pic_arr: PicItem[];
  pic_count: number;
  um_event: UmEvent;
  [key: string]: unknown;
}

export type AdListResponse = ApiListResponse<AdItem>;

/* ============ 11. activityList 活动 ============ */

export interface ActivityListRequest {
  p?: number;
  step?: number;
}

export type ActivityListResponse = ApiListResponse<unknown> & {
  total: number | null;
  currentPage: number | null;
};

/* ============ 12. pullAssistantMsg 助手消息 ============ */

export interface PullAssistantMsgRequest {
  last_time: number; // 上次拉取时间戳，0 = 全量
}

export type PullAssistantMsgResponse = ApiListResponse<unknown>;

/* ============ 14. loadContentSubject 帖子话题 ============ */

export interface LoadContentSubjectRequest {
  content_id: string; // = tid
  type: 'forum';
}

export interface SubjectContent {
  [key: string]: unknown; // 样本中为空数组，未捕获到非空结构
}

export type LoadContentSubjectResponse = ApiListResponse<SubjectContent> | ApiResponse<SubjectContent[]>;

/* ============ 搜索（threadSearch） ============ */

/** GET /mag/circle/v1/Forum/threadSearch?keywords=&p=&step= */
export interface SearchThreadItem {
  tid: string;
  typeid: string;
  sortid: string;
  dateline: string; // "5分钟前"
  lastpost: string;
  iconsrc: unknown[];
  subject: string; // 纯文本标题
  title: string; // 带关键词高亮的 HTML（<span class="active">）
  pics: string[];
  fid: string;
  forum_name: string;
  user_id: string;
  user_name: string;
  user_head: string;
  click: string;
  reply_count: string;
  create_time: string; // unix 秒（字符串）
  last_reply_time: string;
  digest: string;
  special: string;
  is_top: number;
  link: string; // /mag/circle/v1/forum/threadViewPage?tid=...
}

export type ThreadSearchResponse = ApiListResponse<SearchThreadItem>;

/** GET /mag/user/v1/User/userSearch?name=（注意参数是 name 单数） */
export interface UserSearchItem {
  id: number;
  name: string;
  head: string;
  sex: string;
  group_ico_src: string;
  is_vip: number;
  cert_id: number;
  cert_pic_url: string;
  cert_des: string;
  medal_pic_url: string;
  head_pendant_url: string;
  extra?: { sign: string };
  user_id: number;
  user_name: string;
  user_head: string;
  sign: string;
  info: string;
  user_title: string; // 关键词高亮 HTML（<span class="active">）
}

export type UserSearchResponse = ApiListResponse<UserSearchItem>;

/** GET /mag/config/v1/Tools/toolsDefaultGetFour（默认工具 + 热门搜索词） */
export interface ToolsDefaultGetFourResponse extends ApiEnvelope {
  list: ToolItem[];
  hot_search_word_list: Array<{ name: string; link: string }>;
}

/* ============ 写操作：点赞 / 关注（来自第二份抓包） ============ */

/** POST /mag/circle/v1/Forum/contentAddApplaud  body: content_id=<tid>，code=101 表示已点过 */
export interface ContentAddApplaudResponse extends ApiEnvelope {
  user_info?: { user_id: number; user_name: string; user_head: string };
}

/** POST /mag/user/v1/UserRelation/fansAdd  body: user_id=<uid>，code=101 表示已关注 */
export type FansAddResponse = ApiEnvelope;

/* ============ 帖子正文（threadViewPage 内嵌 var row = {...}） ============ */

export interface ThreadAttachment {
  filename: string;
  [key: string]: unknown;
}

export interface ThreadRowData {
  tid: string;
  fid: string;
  pid?: string;
  title?: string;
  content?: string; // 主帖正文 HTML
  forum_name?: string;
  attachment?: ThreadAttachment[];
  [key: string]: unknown;
}

/** GET /mag/circle/v1/Forum/threadList?fid=&p=&step= 版块帖子列表（真实分页） */
export interface ThreadListItem {
  tid: string;
  fid: string;
  sortid: string;
  typeid: string;
  dateline: string; // "06-15"
  lastpost: string; // "5小时前"
  subject: string; // 纯文本标题
  title: string; // 带置顶图标等的 HTML
  des: string;
  user_id: string;
  user_name: string;
  user_head: string;
  pics: Array<{ url: string; tburl: string }>;
  pics_count: number;
  click: string | number;
  reply_count: string | number;
  applaud_count?: string | number;
  is_applaud?: boolean;
  is_top?: number; // 1 置顶
  create_time?: string;
  ip_address?: string;
  special?: string;
  videos?: unknown[];
  iconsrc?: unknown[];
  [key: string]: unknown;
}

export type ThreadListResponse = ApiListResponse<ThreadListItem>;

/* ============ 打赏记录（第三份抓包） ============ */

/** GET /mag/user/v1/reward/listRewardRecord?type=forum&type_value=<tid> */
export interface RewardRecordItem {
  user_id: number;
  head: string;
  name: string;
  reward_type: number;
  reward_time_str: string; // "2024-12-30"
  reward_value: string; // "200歼敌"
  remark: string; // "发帖辛苦啦！打赏奖励!"
  lavish: boolean;
}

export type RewardRecordResponse = ApiListResponse<RewardRecordItem>;

/* ============ 通知 / 圈子树（来自第二份抓包） ============ */

/** GET /mag/user/v3/UserNotify/notifyList?code=at|focus|applaud|comment|chat */
export type NotifyListResponse = ApiListResponse<Record<string, unknown>>;

export interface CircleNode {
  id: number;
  type: number;
  type_value: string; // 即 fid
  name: string;
  icon_url: string;
  des: string;
  link: string; // shuidan://forumThreadList?fid=&circleId=
  isjoined: boolean;
  post_today: string;
  p_circle: number;
}

export interface CircleTreeGroup {
  id: number;
  name: string;
  circle_list: CircleNode[];
}

export type CircleTreeResponse = ApiListResponse<CircleTreeGroup>;

/* ============ 15. commentList 评论 ============ */

export interface CommentListRequest {
  tid: number | string;
  fid: number | string;
  order?: number; // 2 = 最新在前
  p?: number;
  step?: number; // 默认 10
  authorid?: number; // 只看作者，0 = 不过滤
}

export interface CommentItem {
  id: string; // 楼层 pid
  tid: string;
  client: string; // 1 = App
  is_applaud: boolean; // 当前用户是否已赞
  applaud_count: number;
  user_head: string;
  user_id: string;
  user_name: string;
  user_sex: string;
  time?: string; // "8 小时前"
  floor?: number; // 楼层号
  ip_address?: string;
  pics: string[]; // 评论图（可能重复）
  content: string; // HTML 富文本
  [key: string]: unknown; // 楼层时间/回复结构样本未展开
}

export interface CommentListResponse extends ApiListResponse<CommentItem> {
  hot_list: CommentItem[];
  total_page: number;
  step: string;
  page: number;
  count: number;
}

/* ============ 16. getRewardContent 悬赏红包 ============ */

export interface GetRewardContentRequest {
  type_value: string; // = tid
  type: number; // 1 = 帖子
}

export interface RewardContent {
  [key: string]: unknown; // 样本为空数组
}

export type GetRewardContentResponse = ApiListResponse<RewardContent> | ApiResponse<RewardContent[]>;

/* ============ 17. jsApi 当前用户 ============ */

export interface JsApiUser {
  user_id: number;
  name: string;
  head: string;
  sex: string;
  phone: string; // ⚠️ 明文手机号
  device_id: string;
  group_level: string | null;
  is_freeze: number; // -1 正常
  group_name: string; // 用户组名，如 "后勤泡弹员"
  group_ico: string;
  group_ico_src: string;
  is_merchant: number;
  last_sync_time: number;
  is_vip: number; // 2 = 非VIP
  token: string; // ⚠️ 会话 token（与主 Cookie 值一致）
  [key: string]: unknown;
}

/** 注意：无 success/code 包裹，直接 { _USER: ... } */
export interface JsApiResponse {
  _USER: JsApiUser;
}

/* ============ 19/20. 聊天服务 ============ */

export interface ChatUnreadRequest {
  app_key: 'shuidan';
  token: string; // chatToken
  user_id: string; // "user" + 数字ID
  step?: number; // 默认 100
}

export interface ChatUnreadMsgResponse {
  msgs: ChatMsgItem[]; // 未读/未被 App 消费的消息（含自己发出的）
  unRead: number;
}

export interface GroupMsgCountRequest {
  app_key: 'shuidan';
  token: string;
  user_id: string;
}

/** 直接返回数组：[{ 群id, count, ... }] */
export type GroupMsgCountResponse = Array<{
  [key: string]: unknown;
}>;

/* ============ 21. 外链图片代理 ============ */

/** GET /core/attachment/attachment/img?url=<encodeURIComponent(外链)>
 *  响应 302 → /public/download_pic/<md5>.<ext>，直接跟随重定向即可 */
export interface AttachmentImgProxyRequest {
  url: string;
}

/* ============ 22. 用户主页（第四份抓包 sdgun-2026-08-25） ============ */

/** GET /mag/user/v5/user/userHome?user_id= 的 data 部分
 *  注意：name 字段为 RSA 加密串，明文昵称用 loadUsers 或动态条目里的 user.name */
export interface UserHomeData {
  name: string; // RSA 加密（与 mag-ss-key 对应的密文）
  head: string; // 头像原图
  headpic: string; // 头像（另一尺寸）
  sign: string;
  group_name: string; // 用户组，如 "后勤泡弹员"
  group_ico_src: string;
  is_vip: number;
  fans_count: number;
  follow_count: number;
  user_id: string;
  click: number; // 主页访问数
  circleNumber: number;
  showTopicNumber: number;
  isChat: boolean; // 是否可私信
  chat_code: number;
  last_login_time: number; // unix 秒
  is_app_user: boolean;
  sharedata: { title: string; des: string; linkurl: string; picurl: string };
  [key: string]: unknown;
}

/** userHome 顶层的 tab 配置（资料/动态/论坛/圈子，含各 tab 数据链接） */
export interface UserHomeTabConfig {
  id: number;
  config_id: number; // 1 资料 2 动态 3 论坛 4 圈子
  status: number;
  name: string;
  des: string;
  link: string; // 该 tab 的数据接口绝对地址
  system: number;
  [key: string]: unknown;
}

export interface UserHomeResponse extends ApiEnvelope {
  data: UserHomeData;
  tab_config: UserHomeTabConfig[];
  see_me_count: number;
  today_see_me_count: number;
  ip_address: string;
  friend_user_info: { age: string; height: string; weight: string; sex: string };
  [key: string]: unknown;
}

/** GET /mag/user/v1/User/loadUsers?user_ids=1,2 → 明文用户信息（含昵称） */
export interface LoadUserItem {
  user_id: string;
  phone: string; // 明文手机号（敏感，勿外显）
  name: string; // 明文昵称
  head: string;
  sex: string;
  birth: string;
  group_level: string;
  group_name: string;
  sign: string;
  is_chat: number; // 0/1 是否可私信（与 userHome.isChat 语义相反来源不同）
  [key: string]: unknown;
}

export interface LoadUsersResponse extends ApiListResponse<LoadUserItem> {
  total: number;
}

/** GET /mag/user/v3/user/getUserInfo?user_id= → data 为分节数组 */
export interface UserDetailSection {
  type: 'info' | 'tagList' | 'certList' | 'gift' | 'recommend_thread' | string;
  data: unknown; // info: [{name,value}]；tagList: [{id,name,link}]；其余样本为空
  [key: string]: unknown;
}

export interface UserDetailResponse extends ApiEnvelope {
  data: UserDetailSection[];
  is_privacy?: number;
}

/** 动态/帖子条目内嵌的用户对象（注意是 id/name，与 UserBrief 的 user_id/user_name 不同） */
export interface ActivityUser {
  id: string;
  name: string;
  head: string;
  sex?: string;
  group_ico_src?: string;
  [key: string]: unknown;
}

/** 用户动态/帖子条目（userActivityPage 与 userForumOrShowPage 共用结构） */
export interface UserActivityItem {
  tid: string;
  fid: string;
  id?: string;
  title: string;
  pics_arr: PicItem[];
  forum_name: string;
  create_time: string; // unix 秒
  create_time_str: string; // "8小时前"
  applaud_count: number;
  comment_count?: number;
  is_applaud: boolean;
  click: string; // "132阅读"
  link: string;
  type?: number;
  type_info?: unknown;
  user?: ActivityUser; // 含明文昵称 name
  ip_address?: string;
  [key: string]: unknown;
}

/** GET /mag/user/v2/User/userActivityPage | /mag/user/v2/user/userForumOrShowPage */
export interface UserActivityResponse extends ApiEnvelope {
  list: UserActivityItem[];
  last_time: string; // 翻页游标（样本只翻了第 1 页）
  is_privacy: number | string;
}

/* ============ 23. 私信收发（第六份抓包 sdgun-2026-08-25-2） ============ */

/** unReadMsg.msgs[] 与 sendMsg 响应共用的消息体 */
export interface ChatMsgItem {
  body_type: string; // "1" = 文本
  content: string; // JSON 字符串：{"text":"..."}
  method: string; // sendMsgToUser
  msg_id: number;
  time_stamp: number;
  to_user_id: string; // "user<uid>"
  from_user_id: string;
  create_time: number; // 毫秒
  msg_type: string; // "msg"
  ext?: string; // 发送者昵称/头像 JSON（可选，服务端不要求）
}

export interface ChatUnreadMsgsResponse {
  msgs: ChatMsgItem[];
  unRead: number;
}

/** POST shuidan.chat.magcloud.net/msg/sendMsg
 *  sign/sign2：必须存在但实测不校验内容（全零 32 位 hex 可过）；
 *  缺失 sign 报 code=100000「消息签名错误」。msg_id/time_stamp 用当前时间戳。 */
export interface SendChatMsgForm {
  app_key: 'shuidan';
  'data[body_type]': '1';
  'data[content]': string; // JSON.stringify({text})
  'data[method]': 'sendMsgToUser';
  'data[msg_id]': string; // 秒.微秒
  'data[time_stamp]': string;
  'data[to_user_id]': string; // "user<uid>"
  'data[ext]'?: string; // 发送者信息（可选）
  sign: string; // 固定假签名 32 位
  sign2: string;
  timestamp: string; // 秒
  token: string; // chatToken
  type: 'user'; // 单聊
  user_id: string; // "user<自己的uid>"
}

/** sendMsg 响应：无 success/code 之外的包裹直接平铺 */
export interface SendChatMsgResponse {
  success: boolean;
  code?: number;
  msg?: string;
  data: ChatMsgItem;
}

/* ============ 24. 发表评论（第七份抓包 sdgun-2026-08-26） ============ */

/** POST /mag/circle/v1/Forum/replyAdd（表单）：
 *  source=qiniu 固定；tid=帖子；circle_id 来自 threadViewPage 的 var row.circle_id；
 *  content 为纯文本（URL 编码）；auth/video/vest_id 留空 */
export interface ReplyAddForm {
  source: 'qiniu';
  from_push: string;
  tid: string | number;
  circle_id: string | number;
  content: string;
  auth: string;
  video: string;
  vest_id: string;
}

/** 响应 data 即新评论（含 delete_url，can_delete=true 时可删） */
export interface ReplyAddResponse extends ApiEnvelope {
  data: CommentItem & {
    message_result: string; // "post_reply_succeed"
    delete_url?: string; // /mag/circle/v1/forum/postdelete?tid=&pid=
  };
}
