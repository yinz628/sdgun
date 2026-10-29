# SDGun「水弹社区」App 抓包分析报告

> 数据源：9 份抓包（Anything Analyzer 导出，累积 1773 条请求），敏感值（token / 手机号 / 会话）已在文档中脱敏。
> 正文（§0–§4）基于第一份抓包，后续新发现按时间顺序沉淀在附录 1–12（附录 9 为线上实测、附录 11 为本地功能实现、附录 12 为 WAP 源码逆向，均无抓包），**读旧结论前先看对应附录的修正**。

## 抓包与结论索引

| # | 文件 | 条数 | 主题 | 关键结论 |
|---|---|---|---|---|
| 1 | sdgun-2026-08-24.json | 271 | 启动 + 浏览 + 看帖 | 双通道（原生 mag-ss-key / WAP Cookie）；正文在 threadViewPage 的 var row |
| 2 | sdgun-2026-08-24-2.json | 311 | 搜索矩阵 + 写操作 | threadSearch/userSearch 等参数名不一致；点赞/关注 code=101 幂等；签到页 |
| 3 | sdgun-2026-08-24-3.json | 103 | 登录流程 | 完整重放登录可行（附录 2）；短信通道不存在 |
| 4 | sdgun-2026-08-24-4.json | 45 | 启动→登录全流程 | WAF 校验请求头指纹（必须用 requests）；var row 提取 |
| 5 | sdgun-2026-08-25.json | 501 | 频道流翻页 + 用户主页 + 私信入口 | feed 分页 = totalList 游标 + content_id；用户主页接口族 |
| 6 | sdgun-2026-08-25-2.json | 66 | 发送私信 | sendMsg：sign 必须存在但不验内容；unReadMsg 即消息源 |
| 7 | sdgun-2026-08-26.json | 266 | 发表评论 + 看链接帖 | replyAdd；外链证书坑（/link 跳桥）；WAP 页图片改写 |
| 8 | sdgun-2026-08-29.json | 45 | 私信回复收发全链路 | unReadMsg 拉取即清（修正附录 5）；chatToken 稳定；重登不影响网页会话（附录 8） |
| 9 | —（线上实测） | — | 「帖子不存在」的 502 语义 | tid 不存在/不可见统一报 502「您的网络有些问题」；0 阅读无内容帖 = 刚发即删/审核中；含一次误判修正记录（附录 9） |
| 10 | sdgun-2026-09-09.json | 165 | 会话续期 + 新鲜登录报文 | 触发附录 10：WAP 通道明文登录被发现并**实测登录成功**，网页端账号密码登录落地（重放降级为备用） |
| 11 | —（本地功能实现） | — | 关注关键字新帖提醒 | 无上游新接口：复用 v2 频道流做「新帖发现」源，代理侧守护线程轮询 + 本地规则匹配（附录 11） |
| 12 | —（WAP 源码逆向） | — | 评论楼中楼与定向回复 | `commentList` 条目可带 `to_comment` 引用；`replyAdd` 加 `reply_id` 即定向回复（附录 12） |

运维要点速查：会话三个 Cookie / **账号密码登录**（WAP 通道明文表单，附录 10；重放抓包为备用）/
自动签到（每日 08:05 UTC+8）/ **关注关键字监控**（代理侧轮询信息流，附录 11；首次只建基线）/
上游必须 requests（WAF 头序）/ 图片与外链全走代理路由 /
502「您的网络有些问题」= 帖子不存在或不可见（非网络问题，附录 9）/
VPS 侧 Docker 网络（策略路由回程、容器 DNS 固定）三类事故与鉴别见 README「VPS 的网络要点」。

## 0. 总体架构判断

- App 为 **「水弹社区」（shuidan / SDGun 水弹社区）**，基于 **MAGAPPX 混合 App 框架**（magapp.cc 出品，API 前缀 `/mag/` 即来自该框架）。
- 后端为 PHP（响应 `PHPSESSID`、`application/json; charset=utf-8`），统一响应包裹：`{ success, code, msg, ... }`，`code === 100` 表示成功（`101` 为"已完成/重复操作"类语义）。
- 域名分工：
  | 域名 | 作用 |
  |---|---|
  | `mag1.sdgun.net` | 主业务 API + WAP 页面（同一个域） |
  | `app.sdgun.com.cn` | mag1 的别名域（WAP 分享用；代理里按同源路径处理） |
  | `shuidan.chat.magcloud.net` | 独立聊天/私信服务（HTTP，query 鉴权） |
  | `picapp.sdgun.net` | 图片 CDN（七牛） |
  | `bbs.sdgun.com.cn` | 旧 Discuz 论坛（图片缩略图、附件、uc_server 头像 302） |
  | `sdgun.ymgames.com.cn` / `shuidan.app1.magcloud.net` / `pic.magcloud.cc` | 老图/水印图、版块图标、运营图（均在代理白名单） |
  | `e.tb.cn` | 帖内淘宝短链（301 强制 https 且证书不匹配 → 见附录 7-B 的 /link 方案） |
  | `ulogs.umeng.com` / `api.map.baidu.com` / `*.icloud.com.cn` | 友盟统计 / 百度地图 SDK / 系统 iCloud 噪声，与业务无关 |

### 最重要的结论（网页版改造）

抓包里存在**两条并存的请求通道**：

1. **原生通道**（App 壳发起）：携带 `mag-ss-key`（RSA 加密凭据）+ `mag-version` + 定制 UA + Cookie。
2. **WebView/WAP 通道**（App 内嵌网页发起的 XHR）：**只依赖 Cookie + `X-Requested-With: XMLHttpRequest` + `Referer`，完全没有 `mag-ss-key`**。

也就是说 `mag-ss-key` 是原生壳的增强校验，服务端对 WAP 页面只认 Cookie 会话。**网页版直接走 WAP 通道（Cookie 会话）即可绕开 `mag-ss-key` 的生成逻辑**，这是改造成网页版的可行性基础。帖子详情页本身就是服务端渲染的 HTML（`threadViewPage`），网页版可以直接 iframe/内嵌或解析后重组。

---

## 1. 接口清单与功能

### A. 原生 API（带 mag-ss-key）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 1 | POST | `/mag/user/v1/User/repairChatAndResToken` | 修复/换取聊天与红包 token |
| 2 | GET | `/mag/index/v2/index/pages` | 首页框架（频道 Tab、发现页组件，带 find_key/fragment_key 增量缓存） |
| 3 | GET | `/mag/index/v2/index/config` | 全局配置（登录开关、发帖版块、白名单域等）※实际抓到的是 WAP 通道 |
| 4 | GET | `/mag/info/v2/channel/infoListByCatId` | 频道信息流（分页） |
| 5 | GET | `/mag/info/v3/info/infoListByCatId` | 分类信息流（分页） |
| 6 | GET | `/mag/info/v3/channel/channelConfigById` | 频道页组件配置（轮播、工具、横幅…） |
| 7 | GET | `/mag/config/v1/tools/toolsAllGet` | 首页工具集配置 |
| 8 | POST | `/mag/user/v2/user/dayReadFinish` | 每日阅读任务上报完成 |
| 9 | GET | `/mag/user/v3/userNotify/userNotifyIndex` | 通知中心索引（@/关注/点赞/评论/私信计数 + 助手列表） |
| 10 | GET | `/mag/operative/v1/ad/listNotEndByPlace` | 按广告位拉取有效广告 |
| 11 | GET | `/mag/activity/v1/activity/activityList` | 活动列表（分页） |
| 12 | GET | `/mag/operative/v1/assistant/pullAssistantMsg` | 拉取助手（官方号）消息 |

### B. 帖子详情相关（WebView XHR，仅 Cookie）

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 13 | GET | `/mag/circle/v1/forum/threadViewPage?tid=` | 帖子详情页（**HTML**，服务端渲染） |
| 14 | POST | `/mag/circle/v4/show/loadContentSubject` | 帖子话题内容（body: `content_id=<tid>&type=forum`） |
| 15 | GET | `/mag/circle/v2/Forum/commentList` | 评论列表（分页，order=2 倒序） |
| 16 | POST | `/mag/postreward/v1/ReleaseReward/getRewardContent` | 悬赏/红包内容（body: `type_value=<tid>&type=1`） |
| 17 | GET | `/mag/index/v1/Index/jsApi` | 当前登录用户信息（含手机号、token，**敏感**） |
| 18 | GET | `/mag/operative/v1/Ad/listNotEndByPlaceWap` | WAP 广告位 |

### C. 其他

| # | 方法 | URL | 功能 |
|---|------|-----|------|
| 19 | GET | `shuidan.chat.magcloud.net/msg/unReadMsg` | 未读私信（query 鉴权） |
| 20 | GET | `shuidan.chat.magcloud.net/msg/groupMsgCount` | 群聊未读数（query 鉴权） |
| 21 | GET | `mag1.sdgun.net/core/attachment/attachment/img?url=` | 外链图片防盗链代理，302 → `/public/download_pic/<md5>.jpeg` |
| 22 | GET | `bbs.sdgun.com.cn/source/plugin/magmobileapi/magmobileapi.php` | 旧论坛图片缩略图（302 跳转），参数 `mag_mod=forum&mag_fuc=imageThumb&aid=&size=&key=` |

---

## 2. Query / Body 参数释义

### POST /mag/user/v1/User/repairChatAndResToken
- Body (form): `user_id=683237` — 当前用户 ID
- Resp: `data.chatToken` — 聊天服务专用 token（与主站 token 不同）

### GET /mag/index/v2/index/pages
- `find_key` / `fragment_key`：32 位 md5，客户端缓存的"发现页/首页框架"版本键，值不变则可用本地缓存

### GET /mag/info/v2/channel/infoListByCatId
- `channel_id`：频道 ID（来自 index/pages 的 Tab）
- `cat_id`：分类 ID
- `p` / `step`：页码 / 每页条数（20）
- `tab`：Tab 序号；`is_app_first`：是否首屏；`uniqid`：频道唯一标识

### GET /mag/info/v3/info/infoListByCatId
- `cat_id`：分类 ID（如 101=公告）；`is_insert_ad`：是否插入广告位

### GET /mag/info/v3/channel/channelConfigById
- `channel_id`：频道 ID

### GET /mag/circle/v1/forum/threadViewPage
- `tid`：帖子 ID；`circle_id`：圈子 ID（可空）；`mag_hide_progress=1`：隐藏加载进度；`themecolor`：主题色

### POST /mag/circle/v4/show/loadContentSubject
- Body: `content_id`（=tid）、`type`（`forum`）

### GET /mag/circle/v2/Forum/commentList
- `tid`：帖子 ID；`fid`：版块 ID；`order`：排序（2=最新）；`p`/`step`：分页；`authorid`：只看作者（0=不过滤）

### POST /mag/postreward/v1/ReleaseReward/getRewardContent
- Body: `type_value`（=tid）、`type`（1=帖子）

### POST /mag/user/v2/user/dayReadFinish
- Body: `type=readContent` — 上报"完成今日阅读"任务

### GET /mag/operative/v1/ad/listNotEndByPlace
- `place`：广告位标识（如 `pic_slide_1559697851` 首页轮播）

### GET /mag/operative/v1/assistant/pullAssistantMsg
- `last_time`：上次拉取时间戳（0=全量）

### GET shuidan.chat.magcloud.net/msg/*
- `app_key=shuidan`：应用标识（固定）
- `token`：聊天 token（repairChatAndResToken 换取）
- `user_id=user683237`：用户 ID 加 `user` 前缀
- `step=100`（unReadMsg）：单次拉取上限

---

## 3. Header 分析

### 必需（按通道）

| Header | 通道 | 说明 |
|---|---|---|
| `Cookie` | 两者 | **会话核心**。3 个值：`3df5d0fc98d8c119af2e389a3f45b5b0=<会话token>`（cookie 名固定 md5，值=会话 token，与 jsApi 返回的 `token` 一致）、`PHPSESSID=<PHP会话>`、`server_session_434c27a8=<服务器会话>`。网页版登录后由 Set-Cookie 维护即可 |
| `mag-ss-key` | 仅原生 | Base64 密文，长度 128/256 字节，**每个请求都不同**（RSA 随机 padding 加密会话凭据的特征）。网页版走 WAP 通道可**完全不需要** |
| `mag-version` | 仅原生 | `iOS-6.1.3-4.0.0-132`，客户端版本标识，服务端可能做版本校验 |
| `User-Agent`（MAGAPPX 定制） | 两者 | 格式：`MAGAPPX\|<框架版本>\|<设备>\|<app名 shuidan>\|<设备UUID>\|<md5>\|<md5>\|<会话token>`。**末段就是会话 token**；WAP 请求用截断版。网页版可用普通浏览器 UA（threadViewPage 等对 UA 不敏感） |
| `X-Requested-With: XMLHttpRequest` | WAP XHR | jQuery AJAX 标识，服务端据此返回 JSON 而非整页 |
| `Referer` | WAP XHR | 防盗链/上下文校验，建议带上同域页面地址 |
| `Content-Type: application/x-www-form-urlencoded` | POST | 表单编码（无 JSON body） |

### 可忽略（系统自动生成 / 与鉴权无关）

`Host`、`Accept`、`Accept-Encoding`、`Accept-Language`、`Connection`、`Content-Length`、`Origin`、`proxy-connection` —— HTTP 客户端标准行为，照抄或让浏览器自动生成即可。

### 抓包中的噪声（网页版不需要复刻）

- `p230-caldav.icloud.com.cn`：iPhone 系统 iCloud 日历同步，与本 App 无关
- `ulogs.umeng.com/unify_logs`：友盟统计上报
- `api.map.baidu.com/sdkcs/verify`：百度地图 SDK 校验
- `picapp.sdgun.net` / `pic.magcloud.cc`：图片 CDN（直接 `<img>` 引用即可）
- `/public/static/**`：WAP 页面静态资源（Vue2 + jQuery + Vant，说明官方 WAP 本身就是网页，网页版可直接参考）

### 登录提示

登录协议已在后续抓包中完整逆向（`POST /mag/user/v1/user/login`，密码 RSA 加密；
短信通道不可用；完整重放可行）。**最新结论与实现方案见「附录 2 / 附录 3」及 README.md 的
「登录机制」章节**——网页版通过代理 `/auth/replay-exact` 完整重放登录报文换取新会话。

---

## 4. 网页版调用建议

1. 会话：登录后保存三个 Cookie，所有请求同域携带；`X-Requested-With` 与 `Referer` 在 fetch 封装里统一加。
2. 帖子详情：直接加载 `threadViewPage` HTML（官方 WAP 就是这么做的），或解析后用自己的前端重组；评论/红包/话题通过同域 XHR 拿 JSON。
3. 图片：`picapp.sdgun.net` 直连；微信等外链图走 `/core/attachment/attachment/img?url=` 代理（302 跳转）；旧论坛图走 `bbs...magmobileapi.php`（302）。
4. 聊天：`shuidan.chat.magcloud.net` 为 HTTP 明文 + query token，浏览器端注意混合内容限制（HTTPS 页面下需代理转发）。

TypeScript 类型定义见 `sdgun-api-types.ts`。

---

## 附录：第二份抓包（sdgun-2026-08-24-2.json，311 条）新增接口

### 搜索矩阵（搜索页一次并发调用）

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/mag/index/v1/index/searchColumn` | - | 搜索栏目配置（综合/话题/用户/帖子…哪些开启） |
| GET | `/mag/user/v1/User/userSearch` | `name`（单数！） | 用户搜索，`user_title` 带高亮 HTML |
| GET | `/mag/circle/v1/circle/circleSearch` | `keywords`、`step` | 圈子搜索 |
| GET | `/mag/circle/v1/circle/seekCircle` | `keywords` | 圈子查找 |
| GET | `/mag/circle/v1/show/allTopicSearch` | `keyword`（单数）+`highlight=1` | 话题搜索 |
| GET | `/mag/livevideo/v1/Video/videoSearch` | `keywords` | 视频搜索 |
| GET | `/mag/config/v1/tools/searchTools` | `keywords` | 工具搜索 |
| GET | `/mag/operative/v1/ad/globalSearchByPlace` | `keywords`、`place=global_search` | 搜索广告位 |
| GET | `/mag/config/v1/Tools/toolsDefaultGetFour` | - | 默认四工具 + **热门搜索词 hot_search_word_list** |

> 注意各接口参数名不统一：帖子/圈子/视频用 `keywords`，用户用 `name`，话题用 `keyword`。

### 写操作（均为表单 POST，code=101 表示"已做过"）

| 方法 | 路径 | Body | 说明 |
|---|---|---|---|
| POST | `/mag/circle/v1/Forum/contentAddApplaud` | `content_id=<tid>` | 帖子点赞，返回 `user_info`（点赞者） |
| POST | `/mag/user/v1/UserRelation/fansAdd` | `user_id=<uid>` | 关注用户 |

> 抓包上下文：两者都发生在 threadViewPage（tid=4201081）上，fansAdd 的 user_id 即楼主。

### 其他新读接口

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/mag/user/v3/UserNotify/notifyList` | `code=at/focus/applaud/comment/chat` | 分维度通知列表 |
| GET | `/mag/circle/v1/Circle/circleTree` | - | 圈子树（分组 → 版块 fid/名称/图标），可做版块导航 |
| GET | `/mag/circle/v2/show/circleInfoV2` | - | 圈子页信息（动态/视频/好友分类） |
| GET | `/mag/circle/v2/show/contentNew` | `p`、`step` | 动态内容列表 |
| GET | `/mag/addon/v1/sign/signView` | `needlogin=1` | 签到页（HTML） |
| GET | `/mag/user/v1/user/task` | `needlogin=1` | 每日任务页（HTML） |
| GET | `/mag/addon/v1/mall/home` | - | 商城页（HTML） |

---

## 附录 2：第三份抓包（sdgun-2026-08-24-3.json，103 条）——登录流程

### 登录接口

```
POST /mag/user/v1/user/login
Content-Type: application/x-www-form-urlencoded
Body: account=TATang&password=<RSA加密后Base64，256字节>
```

- 原生通道发起（带 mag-ss-key / mag-version / MAGAPPX UA / PHPSESSID 等）。
- 成功响应：`data.token`（即会话 token）、`union_token`、`magim_chat_token`、`cloud_token`、用户信息；`code=100`。
- `Set-Cookie` 下发 `3df5d0fc98d8c119af2e389a3f45b5b0=<token>`（**Max-Age=3600000 ≈ 41 天**）。
- `asyn_ask`：登录后需 GET 一次该 URL（bbs 旧论坛登录态同步，带 uid/t/k 签名）。

### 重要实测结论（网页版登录可行性）

> ⚠️ 2026-09-09 修正：第 2 条的「最终方案」已降级为备用——WAP 通道存在**明文账号密码登录**
> （大写 `User` 的 `/mag/user/v1/User/login`，无需 RSA 公钥与 mag-ss-key），网页端已落地，
> 详见**附录 10**。以下结论针对原生通道仍然成立。

1. **多点会话共存**：手机端重新登录后，旧 token 依然有效 → 网页版与 App 可同时在线。
2. **✅ 完整重放登录已验证可行（原生通道，现为备用方案）**：把抓包里的登录请求**原样**重放（同 mag-ss-key +
   同 PHPSESSID/server_session Cookie + 同密文），服务端接受并签发新 token。
   之前裸重放（缺 mag-ss-key/Cookie）失败并计入 1 次密码错误——**密文本身无时间戳绑定，
   失败原因是缺配套的 mag-ss-key**。代理 `/auth/replay-exact` 即此实现，网页「登录」按钮调用它。
   注意：改密码后需重新录制登录抓包。**密文重放有效期已实测 ≥ 数小时～1 天**
   （08-25 上午的登录抓包，当天上午旧会话失效后重放成功换得新 token c1446630…）。
   该次失效与 App 内重新登录（08-25 抓包 seq30 有一次 login）吻合——见下条第 5 点。
3. **公钥不可获取**：全站 JS（magjs/lyutils/libs 大包）无 RSA 逻辑；getPublicKey 类接口不存在。
4. **短信登录不可用**：`phoneLogin?phone=&code=` 路由存在（参数 phone/code），但发送验证码
   接口在 96 个命名组合 + 多控制器探测中均未找到，App 实测也不发短信。
5. **会话有效期约 41 天**；服务端会不定时使所有旧 token 失效（疑似 App 重新登录时清理），
   代理已做启动自检 + Set-Cookie 吸收 + 一键重放恢复。

### 其他新接口

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/mag/user/v1/User/getUserInfo` | - | 当前用户完整信息（含手机号） |
| GET | `/mag/user/v1/user/userRemarkList` | - | 用户备注列表 |
| GET | `/mag/user/v1/User/blacks` | `step=1000` | 黑名单列表 |
| GET | `/mag/user/v1/reward/listRewardRecord` | `type=forum&type_value=<tid>` | 帖子打赏记录（user/金额/备注） |

---

## 附录 3：第四份抓包（sdgun-2026-08-24-4.json，45 条，App 启动→登录全流程）

### 启动链路新接口

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/mag/index/v1/index/checkForUpdate` | - | 客户端版本检查/升级引导 |
| GET | `/mag/index/v2/index/getChatKeyWords` | - | 聊天敏感词（样本为空） |
| POST | `/mag/index/v1/index/ruiCken` | - | 启动心跳/打卡（code=101 幂等） |
| GET | `/mag/config/v1/layoutConfig/getLccload` | `type=2` | 布局配置懒加载 |
| GET | `/mag/open/cloudAd/getCloudAdIds` | `type=2` | 云广告位 ID |

### 帖子正文的获取方式（重要）

`threadViewPage` 是 Vue 前端渲染页，HTML 里只有空壳 `<div id="htmlData">`；
完整帖子数据在页面脚本的内嵌变量里：

```js
var row = {"tid":"4231648","fid":"153","forum_name":"卫星区",
           "title":"...", "content":"<正文HTML>", "attachment":[...], ...};
// initPage(): document.getElementById('htmlData').innerHTML = row.content
```

提取：定位 `var row =` 后做花括号配对 + 字符串状态扫描（处理 `\"` 转义）切出 JSON 边界再 parse。
`content` 为富文本 HTML：图片用 jquery.lazyload（`src=""` + `data-original=`），需预处理；
付费帖服务端只下发部分正文。实现见 `webui/src/utils/media.ts` 的 `extractThreadRow`。

### WAF 请求指纹（运维关键知识）

上游 WAF 会校验请求头指纹：**httpx 默认的请求头顺序（UA 在末尾）会被判定
"登录信息已过期"（code=1001），requests 的头序（UA 紧随 Host）可通过**。
同一 Cookie、同一接口、仅 HTTP 客户端库不同即结果不同。
⇒ 任何向上游转发的代码必须用 requests（`sdgun_proxy.py` 已按此实现），
换 httpx/aiohttp/curl_cffi 前需先验证头序。

## 附录 4：第五份抓包（sdgun-2026-08-25.json，501 条）——玩具交易区翻页 / 用户主页 / 私信

> 会话：登录后完整操作流：启动 → 登录 → 频道浏览（玩具交易区连续下拉 6 次）→
> 看帖 → 点作者头像进用户主页 → 搜索（075/0755/0575 + 翻页）→ 看帖 → 第二个用户主页。

### A. 玩具交易区（频道信息流 v2）真实分页机制 —— `content_id` 游标

此前结论"v2 feed 服务端不分页"**不完整**。真实机制（已实测复现）：

1. **p=1**：`GET /mag/info/v2/channel/infoListByCatId?channel_id=6&cat_id=3&p=1&step=20&tab=0&uniqid=<频道uniqid>`
   响应顶层除 `list`（20 条 + 混入的广告位）外，还有 **`totalList`：全量有序内容 id 数组**（本例 500 个）。
2. **p>=2**：必须带 `content_id=totalList[(p-1)*step : p*step]`（逗号分隔），服务端按这批 id 批量返回详情。
   **不带 content_id 的 p=2 返回空列表**（这就是之前"只有第 1 页"的原因）。
3. `uniqid` 是每频道固定的设备标识（频道 6 = `5cf71aef0a77c`，频道 9 = `5de9e6ab2f2f1`），实测可省略。
4. 广告位条目标题带【商家广告】、id 不在 totalList 中；信息流条目 `id` 即内容 id，`type_value`=tid。

```
p=1  → list 22 条（20 正常 + 2 广告），totalList 500 个 id
p=2  → content_id=totalList[20:40]  → list 20 条，与 totalList 切片一一对应
p=7  → content_id=totalList[120:140] → 同理
```

### B. 用户主页接口族（点击头像 → 主页）

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/mag/user/v5/user/userHome` | `user_id` | 主页框架：data（头像/签名/用户组/粉丝/关注/访问数/isChat/最后登录），顶层 `tab_config` 是 4 个 tab 及其数据接口链接 |
| GET | `/mag/user/v1/User/loadUsers` | `user_ids`（逗号分隔） | **明文**昵称/生日/等级/签名/`is_chat`；userHome 的 `name` 字段是 RSA 密文，明文从这里取 |
| GET | `/mag/user/v3/user/getUserInfo` | `user_id` | 「资料」tab：data 为分节数组 `[{type:info,data:[{name,value}]},{type:tagList,...},certList, gift, recommend_thread]` |
| GET | `/mag/user/v2/User/userActivityPage` | `user_id,p=1,step=20` | 「动态」tab：list（tid/title/pics_arr/forum_name/create_time_str/applaud/comment/click/user{id,name,head}/ip_address）+ last_time |
| GET | `/mag/user/v2/user/userForumOrShowPage` | `user_id,config_id=3,p,step` | 「论坛」tab；`config_id=4&style_type=pintu` = 「圈子」tab（图墙） |
| GET | `/mag/user/v2/User/userDigestActivity` | `user_id,p,step` | 精华动态（样本为空） |

tab_config（config_id → 数据接口）：
`1 资料 → getUserInfo`；`2 动态 → userActivityPage`；`3 论坛 → userForumOrShowPage?config_id=3`；`4 圈子 → ...&config_id=4&style_type=pintu`。

### C. 私信（环信 Easemob 承载，HTTP 面有限）

| 方法 | 路径 | 参数/体 | 说明 |
|---|---|---|---|
| POST | `/mag/user/v1/User/repairChatAndResToken` | `user_id=<自己的uid>` | → `data.chatToken`（环信密码；账号为 `user<uid>`，如 user683237） |
| GET | `shuidan.chat.magcloud.net/msg/unReadMsg` | `app_key=shuidan&token=<chatToken>&user_id=user<uid>&step=100` | `{msgs:[], unRead:0}`（样本无未读，msgs 结构未见） |
| GET | `shuidan.chat.magcloud.net/msg/groupMsgCount` | 同上（无 step） | 群未读计数数组（样本 `[]`） |
| GET | `/mag/easemob/ChatGroups/getChatGroupAnnouncement` | - | 聊天群公告/配置 `{is_all_silence, chat_allow_recover, ...}` |

**实际消息收发走环信 SDK 的 IM 长连接**（msync 等），不经这些 HTTP 接口，抓包里没有会话列表/发消息的 HTTP 面。
Web 端可实现：换 chatToken、查未读、判断可私信（userHome.`isChat` / loadUsers.`is_chat`）；
完整收发需接环信 Web SDK（账号 user<uid> + chatToken）。

### D. 搜索翻页与头像兜底（补充附录 1）

- `threadSearch` / `userSearch` 均支持 `p`、`step`（App 用 step=10 连续翻页验证有效）。
- 用户头像可能是 `http://bbs.sdgun.com.cn/uc_server/avatar.php?uid=X&size=small[&ts=1&time=...]`，
  302/301 跳转到真实头像或 `noavatar_small.gif`——代理跟随重定向即可（bbs 域名已在白名单）。
- `index/v1/index/search?keywords=&themecolor=` 是 WAP 搜索页（HTML），native 搜索仍用附录 1 的矩阵。

### E. 本份抓包涉及的新类型

`UserHomeResponse` / `UserHomeData` / `UserHomeTabConfig` / `LoadUserItem` /
`UserDetailResponse` / `UserActivityItem` / `ActivityUser` / `InfoListResponse.totalList`
（见 `sdgun-api-types.ts` §22）。

### F. Web UI 对应实现

- 频道流真实分页：`FeedList` 保存 p=1 的 `totalList`，p>=2 切片传 `content_id`；分页组件由估算 total 变为精确 26 页。
- 帖子详情新增楼主栏（`var row` 的 user_id/user_name/user_head），头像与昵称可点击 → 用户主页；评论区用户同样可点击。
- 新增 `UserPanel`：主页头部（明文昵称走 loadUsers）+ 动态/论坛帖子/资料三个 tab + 关注/私信按钮；
  私信弹窗展示 chatToken 与未读（接口实测通过）。

## 附录 5：第六份抓包（sdgun-2026-08-25-2.json，66 条）——私信发送

> 场景：我的页 → 打开用户 287763 主页 → 点私信 → 发送「哥，320还在吗」。
> 核心收获：**私信有 HTTP 发送接口**，附录 4"实际收发只走环信长连接"的结论被修正。

### POST shuidan.chat.magcloud.net/msg/sendMsg（表单编码）

| 字段 | 值/说明 |
|---|---|
| `app_key` | `shuidan` |
| `token` | chatToken（repairChatAndResToken 换取） |
| `user_id` | `user<自己uid>`（如 user683237） |
| `data[to_user_id]` | `user<对方uid>`（如 user287763） |
| `data[body_type]` | `1`（文本） |
| `data[content]` | JSON 字符串 `{"text":"消息内容"}` |
| `data[method]` | `sendMsgToUser` |
| `data[msg_id]` / `data[time_stamp]` | `<秒>.<微秒>` 浮点时间戳（客户端生成） |
| `data[ext]` | 发送者信息 JSON `{"user_name","user_head"}`（**可选**） |
| `type` | `user`（单聊） |
| `timestamp` | 秒级时间戳 |
| `sign` / `sign2` | 32 位十六进制签名 |

响应：`{"success":true,"data":{...消息体, from_user_id, create_time}}`，`data.msg_id` 为服务端整型 id。

### 签名机制实测（关键结论）

- **缺失 sign/sign2 → `{"success":false,"code":100000,"msg":"消息签名错误"}`**
- **签名值不校验内容**：全零 `'0'*32` 也能发送成功（改文本、改收件人、去掉 ext 均可）
- 签名算法未逆出（简单 md5 组合不命中，推测含 App 内置密钥），但服务端只验"存在性"，网页端用固定假签名即可

### 收信：unReadMsg 即消息源

`GET /msg/unReadMsg` 的 `msgs[]` 返回完整消息体（含 `from_user_id`/`to_user_id`/`content`/`create_time`），
**包括自己发出的消息**（未被 App 消费前一直在），按会话双方 id 过滤即可当聊天记录用。
轮询不消费（多次拉取返回相同消息）；App 端读走后消失。历史消息接口（`msgHistory` 存在但参数未命中，恒返回 `[]`）。

### 其他注意

- 上游该接口**偶发 >20s 慢响应**（代理默认 20s 超时可能先断）：超时不代表发送失败，发送后应再拉一次未读确认。
- 聊天服务自带 PHPSESSID Cookie（抓包请求里有），实测不带也能发（鉴权靠 token）。
- Web 实现：`UserPanel` 私信弹窗 = 发送（假签名）+ 每 5s 轮询未读，按 peer 过滤、msg_id 去重、create_time 排序。

## 附录 6：每日签到（来自 08-24-2 抓包的 signView 页面逆向 + 实测）

抓包只覆盖了签到页（未抓到点击动作），接口从页面 Vue 源码逆向并实测验证：

| 接口 | 说明 |
|---|---|
| `GET /mag/addon/v1/sign/signView` | 签到页（HTML）。内嵌 `var row = {...}`：`sign`=1 今日已签/‑1 未签、`sign_continuous_day` 连续天数、`today_sign_count` 全站今日已签人数、`user_id` |
| `GET /mag/addon/v1/sign/signReward?uid=<uid>` | **执行签到**（页面上"签到"按钮 = `reqwest` GET 此接口）。响应 `{"success":true,"data":{"reward_type":1,"gold":{"baseGoldReward":"1",...}}}`；幂等，重复调用返回已签状态 |

奖励规则（row.sign_rule）：每天 1 歼敌；连签 7 天 +3、一个月 +20、一年 +100。
相关只读页：`signRecordList`（签到记录/补签）、`rankListViewv2`（今日排行）。

### Web 实现（自动签到）

- 代理内置守护线程：每日 `SDGUN_SIGN_TIME`（默认 08:05，北京时间 UTC+8，
  避免容器 UTC 时区跨日错签）自动签到；启动时若当天未签且已过时刻会**补签**；
  失败每 10 分钟重试。状态落盘 `sdgun_sign.json`（`last_signed_date` 防重复）。
- REST：`GET /sign/status`（实时上游状态 + 本地记录）、`POST /sign/run`（手动立即签到，幂等）。
- 前端「每日签到（自动）」卡片：今日状态/连续天数/全站人数/最近一次结果 + 立即签到按钮。
- ⚠️ 踩坑记录：`sdgun_sign.json` 会被 `sdgun*.json` 抓包通配误识别导致启动崩溃，
  已在 `_capture_files()` 排除（`_extract_capture_jar` 同时加了 isinstance 防御）。

## 附录 7：发表评论 + 帖内链接修复（第七份抓包 sdgun-2026-08-26.json，266 条）

### A. 发表评论

**`POST /mag/circle/v1/Forum/replyAdd`**（表单编码）：

| 字段 | 值 |
|---|---|
| `source` | `qiniu`（固定） |
| `tid` | 帖子 id |
| `circle_id` | 来自 threadViewPage 的 `var row.circle_id`（如玩具交易区=185） |
| `content` | 纯文本（URL 编码） |
| `from_push`/`auth`/`video`/`vest_id` | 留空 |

响应 `data` 即新评论（含 `id`(pid)、`time`、`floor`、`can_delete`、
`delete_url=/mag/circle/v1/forum/postdelete?tid=&pid=`、`message_result:"post_reply_succeed"`）。
实测：发评成功；**删除评论 = GET delete_url**（返回 `data:[]`）。

### B. 帖内链接"您的连接不是私密连接"的根因与修复

根因：帖子里的淘宝短链 `http://e.tb.cn/**` 服务端 **301 强制升级 https**，
而 e.tb.cn:443 返回的证书是 `*.alibabacorp.com`（域名不匹配）→
浏览器报 NET::ERR_CERT_COMMON_NAME_INVALID。任何浏览器直连都无法通过。

修复（代理新增 `/link?url=` 跳桥路由，仅 GET、不携带任何 sdgun Cookie）：
1. 服务端 `verify=False` 抓取；**同主机重定向（http→https 升级）服务端继续跟**（最多 5 跳），
   避免浏览器撞坏证书；**跨主机重定向**把 Location 透传给浏览器（目标证书通常正常）
2. 2xx 落地页同源返回，由页面自身 JS 跳到真实地址（实测短链 → item.taobao.com 正常登录/商品页）

前端 `prepareCommentHtml` 增加链接改写：
- `mag1.sdgun.net` / `app.sdgun.com.cn` 绝对地址 → 去域名走本站同源路径（代理路由承接 WAP 页）
- 其余 http(s) 外链 → `/link?url=`；统一 `target=_blank rel=noopener` 不离开应用

### C. WAP 页（threadWapPage 等）图片不显示的修复

现象：同源代理打开的 WAP 页里图片全部裂图。原因：WAP 页 HTML 原样透传，
图片直指图床（picapp 等），浏览器直连不可达（SPA 里是前端改写，WAP 页没有）。

修复（代理层，forward 对 text/html 响应统一处理 `_rewrite_html_images`）：
- 把 `src`/`data-original` 指向图床白名单（picapp/bbs/pic.magcloud.cc/ymgames/app1）
  的 URL 改写为 `/proxy?url=…`
- 兼容四种形态：普通 HTML、lazyload 空占位、**var row JSON 内的转义形态**
  （`src=\"http:\/\/picapp…\"`，斜杠带反斜杠、值尾带转义引号）、协议相对 `//host/`
- 改写后 `var row` JSON 仍可正常解析（已验证）；前端 `isProxied()` 防二次包装
- `/proxy` 改为跟随重定向（bbs `uc_server/avatar.php` 会 302 到真实头像）
- 白名单补充：`sdgun.ymgames.com.cn`（水印老图）、`shuidan.app1.magcloud.net`（版块图标）

注意：`<script src>` 等静态资源不改写（白名单只含图床域名，不含 mag1），
WAP 页自己的 CSS/JS 是相对路径，天然走代理。

### D. 会话失效处置记录（08-26）

用户在 App 内重新登录后旧 token 再次失效（与附录 3 结论一致）。
处置：抓包目录放入含新登录记录的 sdgun-2026-08-26.json → 本地/VPS 各执行一次
`POST /auth/replay-exact` → 均成功换得新会话；VPS 会话已同步回本地。
重放有效期进一步确认：跨天（前一天录的包次日重放成功）。

## 附录 8：第八份抓包（sdgun-2026-08-29.json，45 条）——私信回复收发全链路复核

> 场景：App 收到用户 599715（小豆苗）回复「没有了 被预定了」→ App 内重新登录
> （第 9 条记录）→ 换发新 chatToken → 发送「好，谢谢」。整条私信链路在一份抓包里闭环。

### 1. 发送实现逐字段复核（与附录 5 对照）

真实 sendMsg 表单与本网页实现（`sdgunApi.sendChatMsg`）逐字段一致：
`app_key / data[body_type]=1 / data[content]={"text":…} / data[method]=sendMsgToUser /
data[msg_id]=<秒>.<微秒> / data[time_stamp] / data[to_user_id]=user<uid> / data[ext] /
timestamp / token / type=user / user_id`。
差异仅两处且均无影响：真实 `sign`/`sign2` 是 32 位十六进制（非全零）——但换新 token 后
全零假签名依旧发送成功，"只验存在性"结论再次证实；`data[ext]` 真实端恒带，网页端登录态下也恒带。

### 2. unReadMsg 消费语义修正（修正附录 5）

附录 5 曾记「轮询不消费（多次拉取返回相同消息）」。本次时间线：

```
#14 unReadMsg(tok=132915aa) → msgs=[小豆苗的回复 18:14]
#21 unReadMsg(tok=132915aa) → msgs=[]   ← 首次拉取后即消失
#29/#32/#37 unReadMsg       → msgs=[]
```

两份抓包合并后的结论：**收到的消息首次拉取即被消费**（谁先拉谁拿到，App 与网页端共用
同一 chatToken 消费池）；附录 5 观察到的"重复返回"应是自己发出的消息（self-sent
不属于未读，会持续在列）。对网页端的影响：
- 打开会话时的首次 `pollUnread` 能接到「App 尚未读走」的回复（UserPanel 已实现）；
- 一旦被 App 或网页拉走，该消息从服务端消失——页面刷新后无法找回（无历史接口），
  这就是 UI 仅显示"打开后收发的消息"的根本原因，属上游限制而非缺陷。

### 3. chatToken 稳定性与重登影响

- `repairChatAndResToken` 在网页端换出的 token（fc283d76…）与 App 端最新抓包完全一致
  ——该接口是"取当前生效 token"而非"签发新 token"，重复调用无副作用。
- App 重新登录后，旧 chatToken（132915aa…）在一段时间内仍能调用 unReadMsg（本抓包
  #21/#29 仍返回 200），新 token 并行有效；最终以 repairChatAndResToken 取到的为准。
- App 内重新登录（#9 的 POST user/login）后，代理网页会话经 `/health` 实测仍 ok
  ——主站 Cookie 多点共存结论再次证实，无需重放登录。

### 4. groupMsgCount（仅记录，无实现价值）

`GET /msg/groupMsgCount?app_key&token&user_id`，本抓包 5 次调用与实测均返回 `[]`
（无群消息时的空数组），非空结构仍未见到。暂无前端落地价值。


## 附录 9：「帖子不存在」被上游统一报成 502「您的网络有些问题」——兼记一次误判修正（2026-09-07，线上实测，无抓包）

**现象**：VPS 网页端偶见「0 阅读、无正文」的帖子，点开报
`评论加载失败：您的网络有些问题`（例 tid=4269365）。用户观察到"之前也有"。

**结论（先说）**：上游对「tid 不存在或不可见」的帖子，JSON 接口统一返回
`{"success":false,"msg":"您的网络有些问题","code":502}`，HTML 正文页返回同一文案的
错误页（HTTP 仍 200）。**与网络无关，也不存在站方故障**——网页端遇到的这类帖子，
就是「发表后很快被删除」「审核未通过/暂不可见」等状态的帖子：列表 feed 是发布瞬间
抓到的，点击详情在删除之后，于是"列表里有、点开 502"；「0 阅读」正是存活时间极短
（或从未公开可见）的体现。

**决定性对照**（经代理对上游实测，游客态与会话态结果一致）：

| tid | 状态 | v2 commentList | v1 commentList | threadViewPage |
|---|---|---|---|---|
| 4269545（当时最新帖） | 存在 | OK | OK | 200，var row 正常 |
| 4269365（用户例） | 不存在/不可见 | 502:您的网络有些问题 | 同左 | 200 错误页（同文案） |
| 4269091 | 同上 | 同上 | 同上 | 同上 |
| 99999999 / 123 | 从不存在 / 远古空洞 | 同上 | 同上 | 同上 |

**误判记录（重要，避免重蹈）**：当天早些时候曾据「tid ≥ 4269507 全部失败、
≤ 4269506 全部正常」且边界两侧仅差 52 秒，误判为"站方自 4269507 起新帖详情接口
全挂的持续性故障"。根因：**探测了一批当时尚不存在的 tid**（4269507 在首次探测约
5 分钟后才发布），把"不存在"误读成"故障"；其后观察到的"边界随时间上移"实为新帖
持续发布，并非"自愈"。正确鉴别手段是**对照组**：拿从不可能存在的 tid（99999999）
与故障 tid 比对错误签名——完全一致即为"不存在/不可见"，而非"坏了"。

**v1/v2 commentList 关系**：对存在的帖子，两版参数/响应逐字段一致（热帖
count=1264、列表内容、p1–p3 分页、order=1/2 排序全部相同）；对不存在的帖子两版
同错。webui 保留 v2→v1 防御性回落（`sdgunApi.comments`），仅用于抵御单一接口
版本波动。

**上游「真 502」的鉴别（2026-09-09 晚补充）**：本项目的 502 有三种来源，形状各异——
① JSON `code=502`「您的网络有些问题」= 帖子不存在/不可见（本附录主题）；
② 代理返回的 JSON `{"proxy_error": ...}` = 我们到上游的网络层失败（重试耗尽）；
③ **上游自身的 nginx 502 HTML 页**（`<center><h1>502 Bad Gateway</h1>`）或
`SQLSTATE[HY000] [2002] Connection refused` JSON = **sdgun 服务端故障**（实测其
数据库宕机会让 jsApi/信息流/搜索全挂，且启动自检会误报"所有候选 Cookie 无登录态"
——是上游无法校验会话，不是会话真失效）。鉴别手段：看响应体形状；手机 App 同时
报错即为上游问题，等其自愈即可，会话在恢复后自动重新校验通过。

**前端落地**（webui，后端代理无改动）：
- `ThreadPanel`：识别 threadViewPage 的 200 错误页（`css/user/error-` 标记/错误文案），
  正文区显示「该帖已不可访问（可能发表后即被删除、审核中或不存在）」，
  与「本来就没有正文（付费/隐藏/纯图帖）」区分开；
- code=502 的评论报错显示「帖子不存在或已不可见（可能刚被删除或审核中）」，
  不再透传上游误导性文案「您的网络有些问题」。

---

## 附录 10：WAP 通道明文登录——网页端账号密码登录（2026-09-09 发现，推翻附录 2「仅重放」结论）

背景：会话再次失效，用 sdgun-2026-09-09.json（165 条，含当日新鲜录制的登录请求）恢复会话时，
顺带把「真·账号密码登录」重新调研了一遍——结论是**可行**，只是不在 App 原生通道上。

### 发现过程

- 附录 2 的「公钥不外发 → 只能重放」只对**原生通道**（小写 `/mag/user/v1/user/login`，
  RSA 密文 + mag-ss-key）成立；启动→登录全流程抓包（附录 3）显示 App 登录前不加载任何
  服务端 JS，加密在原生壳内，密钥确实拿不到。
- 突破口是 WAP 站自己的登录页：`lyutils.js` 的 `toWapLogin` → `/mag/wap/v1/wap/waphome/login`。
  该页是 Vue SPA（`/public/wap/dist/static/js/` 下的分包），登录组件在懒加载 chunk 27
  （`27.0e659508fa382928bc6a.js`，chunk 哈希表在 manifest.js）。
- 组件源码（webpack 模块 530）`normalLoginMethod`：
  `this.$http.post("/mag/user/v1/User/login", this.user, …)`，其中
  `user = {account, password, phone, code, security_code, country_code}`——**明文提交，
  无任何加密**；`$http` 封装为 axios + qs（`application/x-www-form-urlencoded`）。
- 普通登录**无图形验证码**；验证码（security_code，code=1009 触发）只用于短信通道的
  发送验证码。成功后页面会顺手 POST 响应里的 `asyn_ask`（bbs 旧论坛会话同步）。

### 线上实测（2026-09-09）

- `POST https://mag1.sdgun.net/mag/user/v1/User/login`，表单 `account=<不存在的账号>&password=…`
  （+ 空串占位字段），App UA + requests 指纹：
  `{"success":false,"msg":"账号或密码错误…（当前已失败 1/3 次）","code":0}`
- 端点在 **mag1** 域名即可用（无需走 app.sdgun.com.cn）；接受明文；**不需要 mag-ss-key**；
  requests 头序可通过。
- ⚠️ 失败响应带**临时锁定计数**（3 次锁定），且不存在 的账号也计数——
  计数疑似绑定来源 IP，切勿拿错误凭据反复探测。

### 代理与前端落地

- 代理 `POST /auth/login {account, password}`：代为提交明文表单（带当前会话 PHPSESSID），
  成功后吸收 Set-Cookie + `data.token`，触发 `asyn_ask`（GET 无 Cookie，签名全在 URL），
  只回传安全字段（user_id/name/head/group_name/group_id；响应含手机号与多枚 token，不透传）。
- 空账号/密码在代理层直接 400，不浪费锁定计数；上游失败 msg（含计数）原样透传到 UI。
- 前端 UserBar：「登录」按钮改为账号密码弹窗；重放登录（`/auth/replay-exact`）降级为
  备用入口（「用最新抓包重放登录（免输密码）」）。

### 结论修正

- 「重放是最终方案」降级为**备用**：主路是账号密码登录（WAP 通道）；改密码后直接可登录，
  无需重录抓包；重放的「密文有效期数小时～1 天」限制不再关键。
- 附录 2 第 3 条「公钥不可获取」对原生通道仍成立，但 WAP 通道登录根本不需要公钥。
- 安全提醒：这条通道是明文表单，网页端登录等于把密码交给本机代理转发——仅限本地/可信
  局域网使用，切勿暴露公网（与现有 README 部署警告一致）。

### 落地验证与后续观察（2026-09-09 晚）

- ✅ **真实凭据端到端登录成功**（VPS http://192.168.2.197:50002 网页弹窗提交，
  代理 `/auth/login` 换得新会话，jsApi 恢复登录态）——功能正式可用。
- 📉 **同日观察：抓包派生会话的寿命极不可靠**。当天 19:59（UTC+8）录的新抓包，
  其会话 20:59 校验仍通过，21:15 即被上游清理（期间无网页端操作，疑似手机 App
  端活动触发）。结合附录 2 第 5 点「服务端不定时清理 token」——**抓包会话随时可能
  失效，账号密码登录才是可持续的正路**；抓包仅保留作冷启动兜底。
- 运维插曲（记录在 README VPS 章节）：部署当晚 VPS 的 `sdgun-upstream-routes`
  直连路由服务 inactive+disabled，容器到上游整体断连，症状与「会话全失效」雷同
  （启动自检所有候选报"无登录态"）。上游"全候选失败"时先查路由/连通性再怀疑 Cookie。

---

## 附录 11：关注关键字监控（本地功能，2026-10 实现，无抓包）

背景：想要「盯住某些关键字的新帖」（如二手交易区的「出 + glock」）。上游没有订阅/关键词
推送接口，只有信息流列表。本功能是**纯本地实现**：规则、命中、已见集合全在代理进程内维护，
没有新增任何上游接口调用——用的仍是附录 4/5 已逆向的首页框架与 v2 频道流。

### 路由表（sdgun_watch.py，`/watch/**`）

| 路由 | 说明 |
|---|---|
| `GET /watch/status` | `{enabled, interval, source, rule_count, hit_count, polls, last_ok_at, last_error, last_error_at}` |
| `GET /watch/rules` | `{rules: [...]}` |
| `POST /watch/rules` | body `{name?, keywords: str\|list}` → `{rule, rules}`；校验失败 400 `{msg}` |
| `POST /watch/rules/remove` | body `{id}` → `{removed, rules}` |
| `POST /watch/rules/toggle` | body `{id}` → `{rule\|null, rules}`（停用而不删除，便于临时静音） |
| `GET /watch/hits?since=-1&limit=100` | `{hits, max_hit_id, total}`；`since<0` 取最新，`since>=0` 取 `hit_id>since`（前端游标） |
| `GET /watch/settings` | `{window_hours}`（提醒时间窗，12/24/48） |
| `POST /watch/settings` | body `{window_hours}` → `{window_hours}`；非法值 400 `{msg}` |
| `POST /watch/run` | 立即检测一次（线程池执行 `poll_once`） |

### 上游调用（两种源形态）

1. 监控源解析（只需成功一次，结果缓存进 `sdgun_watch.json` 的 `source`）。优先级：
   `SDGUN_WATCH_FID` > `SDGUN_WATCH_CHANNEL_ID`+`SDGUN_WATCH_CAT_ID` > 缓存 > 自动解析；
   自动解析为 `GET /mag/index/v2/index/pages` → `index_fragment`（JSON 字符串）→ 第一个
   `column_type==7` 的频道（实测「玩具交易区」）→ `GET /mag/info/v3/channel/channelConfigById?channel_id=` →
   优先 `cat_name` 含「新帖」的分类，否则 `info_config[0]`。
2. 每轮轮询按源形态二选一：
   - **版块源（`fid`）**：`GET /mag/circle/v1/Forum/threadList?fid=&p=1&step=50`。
     条目字段名与频道流完全不同（`tid`/`subject`/`dateline`/`forum_name`），
     由 `_normalize_thread_item` 归一化成 ingest 认的形态（`id`/`type=2`/`type_value`/`title`/…）。
     该接口**没有 `totalList`**：置顶旧帖靠 `seen_ids` 去重自然吸收（首轮只建基线，不会误报）。
   - **频道分类源（`channel_id`+`cat_id`）**：`GET /mag/info/v2/channel/infoListByCatId?channel_id=&cat_id=&p=1&step=50&tab=0&is_app_first=-1`，
     取 `list` 条目 + `totalList`（全量有序 id，用于过滤广告位）。
3. 全部走 `sdgun_session.upstream_client`（requests，WAF 头序）并 `store.absorb(resp)`，
   Cookie 取 `store.cookie_header()`。
4. **换源自动重建基线**：`poll_once` 在解析出源后比较「实际轮询目标」（`fid` 或
   `channel_id`+`cat_id` 组合；`resolved_at` 每轮都变，不能直接比整个 dict），不同则
   `reset_baseline()`（清空 `seen_ids`、`initialized=false`，**规则与命中历史保留**）再继续本轮。
   旧目标必须在解析**之前**读取——`_resolve_source` 命中 env/自动解析时会先把新源落盘，
   之后再比就永远相等、换源不会重建基线（会刷屏）。

### sdgun_watch.json 结构（DATA_DIR，VPS 挂载卷）

```json
{
  "rules": [{"id": 1, "name": "", "keywords": ["出", "glock"], "enabled": true, "created_at": 1700000000}],
  "hits": [{"hit_id": 1, "tid": 123, "content_id": 456, "title": "...", "user_name": "...",
            "publish_time_str": "1小时前", "forum_name": "玩具交易区",
            "keywords": ["出"], "rule_ids": [1], "created_at": 1700000000}],
  "next_rule_id": 2, "next_hit_id": 2,
  "seen_ids": [456, 457],
  "initialized": false,
  "source": {"fid": 176, "forum_name": "", "resolved_at": 1700000000},
  "poll": {"polls": 0, "last_ok_at": 0, "last_error": null, "last_error_at": 0},
  "settings": {"window_hours": 24}
}
```

（`source` 有两种形态：版块源 `{"fid": …, "forum_name": …}` 与频道分类源
`{"channel_id": …, "cat_id": …, "channel_name": …}`。）

上限：`hits ≤ 1000`（`MAX_HITS`）、`seen_ids ≤ 30000`（`MAX_SEEN_IDS`，FIFO 裁剪，最旧先出）；
规则 ≤ 50 条、每条 ≤ 8 个关键字、单关键字 ≤ 40 字符。原子落盘（`atomic_write_json`）。

**保留策略的两个不变式**（2026-10-22 多日运行后的反馈修复）：

1. `seen_ids` 必须覆盖「最长时间窗（48h）× 峰值发帖速率」——实测交易区约 100+ 帖/小时，
   48h ≈ 5000 条，30000 给了 6 倍余量。原值 2000 约 20 小时即被 FIFO 挤掉，短于 48h 窗口，
   于是被回帖翻起的老帖会因「已见记录已丢失」重复命中。
2. 命中另按 **tid 兜底去重**（`_hit_tids`，不单独持久化，从已加载 `hits` 重建、随命中裁剪同步
   移除）：生成命中前若该 `tid` 已有命中记录在保留期内则跳过（仍记 `seen_ids`）。这样即使
   `seen_ids` 被裁剪、或因换源 `reset_baseline` 被清空，同一条帖子也不会二次提醒；只有它的
   命中记录本身被 FIFO 裁掉（>1000 条之后）才允许重新命中。

### 设计取舍

- **为什么在代理侧轮询，而不是前端匹配**：① 页面不开也要能发现（前端轮询只在标签页存活期间
  有效）；② 上游请求必须带 App 身份头与 Cookie，前端直连会被 WAF 挡（附录 3/4）；
  ③ 与签到/私信归档同一模式（守护线程 + 本地 JSON），前端只做展示与规则管理。
- **为什么用 v2 频道流做源、并用 `totalList` 过滤广告**：v2 频道流是唯一带「全量有序 id」
  的列表接口（附录 5 的翻页游标就是它），`p=1` 的 `totalList` 可判定条目是否为真实内容
  （广告位条目不在其中）；再叠加「标题含**商家广告**恒丢弃」的兜底。`step=50` 是取「一屏
  最新的量」——间隔 60s 时新帖通常只有个位数，50 条足够覆盖；`seen_ids` 保证不重复命中。
- **为什么还需要版块源（`fid`）**：频道分类流是**策展视图**，会漏新帖（见「上线实测与修正」
  第三条），交易类关键字建议直接用版块 `threadList`；代价是它没有 `totalList`，广告过滤
  只能靠「标题含商家广告」那条兜底。
- **基线语义**：`initialized=false` 的首轮只把当前列表 id 记入 `seen_ids`，**不产生命中**。
  否则首次部署就会把首页 50 条历史帖全部弹给用户。命中因此「自部署（或首次轮询）起累积」，
  不回溯历史帖；**换监控源同样自动重建基线**（规则与命中历史保留）；删掉 `sdgun_watch.json`
  等价于重建基线（旧的命中记录一并丢失）。
- **匹配语义**：规则内多关键字 **AND**（全部 casefold 包含于标题 casefold），规则之间 **OR**；
  停用的规则不参与匹配（保留规则便于改回）。
- **提醒时间窗（`settings.window_hours`，默认 24，取值 12/24/48）**：生成命中前检查条目
  `create_time`（epoch 秒）是否早于 `now - window_hours*3600`，是则只记 `seen_ids`、
  不产生命中——这是对「旧帖被算法翻出」最直接的压制手段。**没有 `create_time` 的条目
  （如频道源）一律放行**（fail-open）：宁可多提醒一次，也不要因为上游少给一个字段就整片漏帖。
  该值由前端抽屉里的「只提醒 N 小时内发布」下拉通过 `POST /watch/settings` 改，服务端持久化。
- **已读/未读是纯前端本地状态**：上游没有"已读"概念。前端把**已读帖子 tid** 存浏览器
  localStorage（`sdgunWatchReadTids`，上限 2000 条只留最新——不小于后端 `MAX_HITS` 1000，
  免得已读记录先于命中被裁掉），命中列表分「未读/已读」两类、按最新在上展示，点开即已读；
  顶栏角标 = 未读命中数（刷新页面不清零）。**按 tid 而不是 hit_id**：同一个帖子会换 hit_id
  （历史去重只留最新一条、hits 超 1000 条 FIFO 裁剪、seen 丢失后重新命中），按 hit_id 记
  会表现为"之前已读的帖子又变回未读"（用户实测反馈）；旧版本按 hit_id 存的数据在读取时
  用命中列表自动迁移（`migrateWatchReadFromHitIds`，幂等）。与通知游标
  `sdgunWatchSeen`（决定"弹过提醒没有"）是两件互不相干的事。
- **一切以「发帖时间」为准，而不是列表顺序/回帖时间**（2026-10-22 用户反馈）：版块
  `threadList` 实为**最后活动置顶**排序（实测：昨天创建的帖因有新回复，排在 2 小时前的帖
  上面），因此 ① 命中记录存 `publish_time`（发帖 epoch 秒，来自条目 `create_time`），前端
  按它倒序展示（`publish_time` 为 0 的旧记录回退 `created_at`）；② 生成命中前用**规则创建
  时间**再过滤一道——只有发布晚于（或等于）规则 `created_at` 的帖才算命中，多规则命中时只
  保留满足者（`keywords`/`rule_ids` 也只含它们），全部不满足则该帖不生成命中（仍记 `seen_ids`）。
  语义是"规则从创建那一刻起盯新帖"，与基线不回溯一致，消除按回帖时间触发的误报。
  无 `create_time` 的条目（频道源）两道门槛都放行（fail-open）。

### 上线实测与修正（2026-10-22）

功能上线后真实数据暴露三点，均已修正：

- **聚焦流的条目不能按 `type_value` 取 tid**：频道 6 的第一个分类 `cat_id=1`「无余」是
  聚焦精选流，条目 `type=6`、`type_value` 恒为分类 id（实测 `'198'`），按 `type_value`
  取 tid 会把通知指向错误帖子。修正为与前端 `media.threadTid` 同一口径：条目 `link` 里的
  `tid=` 优先 → 否则 `type==2` 的 `type_value` → 否则条目 `id`。实测「今日新帖/玩具交易」
  流条目 `type=2`、`type_value` 即真实 tid（如 `'4376213'`）。
- **自动解析优先「新帖」类分类**：原实现取 `info_config[0]`，线上选中「无余」（聚焦精选流：
  `type=6` 混合内容、`totalList` 为空、混入旧帖），不适合「新帖发现」。现改为优先取
  `cat_name` 含「新帖」的分类，找不到才用第一个。
- **频道流是策展视图，会漏新帖 → 新增版块源（`SDGUN_WATCH_FID`）**：用户反馈「搜索里能看到
  新帖、关注列表没有」。实测（2026-10-22）玩具交易区 7 分钟前 / 11 分钟前的新帖**不在**
  频道 `cat3` `p=1` 的 50 条里，而版块 `threadList?fid=176` 前 50 条就包含它们——频道分类流
  是运营策展视图（只收精选/推荐内容），版块列表才是该版块的全量时间序。故新增 `fid` 源
  （`_fetch_feed` 分支 + `_normalize_thread_item` 字段归一化），并新增**换源自动重建基线**
  （否则切源瞬间新源里满屏旧帖会被当新帖弹给用户）。
  **交易类关键字建议直接用 `SDGUN_WATCH_FID`**；VPS 部署固定
  `-e SDGUN_WATCH_FID=176`（玩具交易区，`deploy/deploy_common.py` 的 `DOCKER_RUN`）。

### 已知限制

- **旧帖被算法重新翻出可能误报**：列表没有可信的发布时间序——版块 `threadList` 是
  「最后活动置顶」排序，频道源条目只带相对时间文案 `publish_time_str`，判定「新帖」只能靠
  `seen_ids` 集合。现已加**提醒时间窗**（`settings.window_hours`）与**规则创建时间门槛**
  （都基于版块源的 `create_time` 精确过滤，见「设计取舍」），把窗口外的旧帖、规则创建前
  发布的旧帖一并压掉；但频道源没有该字段、走 fail-open，故这类源仍可能偶发一次误报。
- 监控用的是代理当前会话，会话失效期间轮询失败（`last_error` 留痕、面板脚注展示），
  重新登录后自动恢复；`SDGUN_WATCH_INTERVAL` 最小 20 秒，不要调得比上游缓存更激进。
- **频道分类流是策展视图，可能漏帖**：实测玩具交易区的新帖会缺席 `cat3` 的首页 50 条
  （见「上线实测与修正」第三条），因此「监控源确实覆盖目标帖」是使用前提。要覆盖某个版块
  的全量新帖，用 `SDGUN_WATCH_FID=<版块 fid>` 指定版块源；要盯别的时间序频道则用
  `SDGUN_WATCH_CHANNEL_ID` + `SDGUN_WATCH_CAT_ID`（**两者都设**）。优先级
  `SDGUN_WATCH_FID` > channel/cat > 缓存 > 自动解析，两者同时设时以 fid 为准；
  换源会自动重建基线（规则与命中历史保留），不需要手动删 `sdgun_watch.json`。

---

## 附录 12：评论楼中楼与定向回复（WAP 源码逆向 + 实测样本，无新抓包）

背景：网页端评论区此前只能发普通评论，也看不到"回复某条评论"的引用关系。上游其实早就
支持楼中楼——`commentList` 的条目带引用对象，`replyAdd` 多一个 `reply_id` 参数即可定向回复。

### `to_comment`：被回复评论的引用

`commentList`（v1/v2 响应结构一致，附录 1/附录 9 已确认两版逐字段相同）每条评论可带
`to_comment`，为空时上游给 **`[]` 或 `null`**（两种形态都出现过，前端按"没有 `id` 就当不存在"处理）：

```json
{
  "id": "<被回复评论的 pid>",
  "user_id": "<被回复者 user_id>",
  "user_name": "<被回复者昵称>",
  "user_head": "<被回复者头像>",
  "content": "<被回复评论的正文 HTML>",
  "time": "<相对时间文案>",
  "floor": "<楼层号>",
  "is_publisher": "<是否楼主>"
}
```

要点：

- **回复与主评论平铺在同一个 `list` 里**，没有嵌套结构——楼中楼关系只由 `to_comment` 表达，
  按 `to_comment.id` 反查即可还原引用关系。
- `content` 是**富文本 HTML**，来自社区用户，渲染前必须过消毒链路
  （`media.ts renderRichText` = `prepareCommentHtml` → `sanitizeHtml`）。
- `id` 是**字符串**（与 `CommentItem.id` 同一口径，即楼层 pid）；`floor` 上游有时给数字有时给字符串。

### `reply_id`：定向回复参数

回复某条评论用的是**同一个** `POST /mag/circle/v1/Forum/replyAdd`（附录 7），表单只多一个
`reply_id=<目标评论 id>`，其余字段（`source=qiniu`/`tid`/`circle_id`/`content`/`auth`/`video`/
`vest_id`/`from_push`）与普通评论完全相同。来源是 WAP 源码的 `replyCmtItem`：

```js
onCommentAction({ tid, reply_id: o.id, circle_id, content, ... })
```

即 `reply_id` 直接取被回复评论对象的 `id`（不是 tid、不是 floor），与 `to_comment.id` 是同一套 id。

### 与 `replyAddV1`（Discuz 帖）的关系

上游另有一个 `replyAddV1`，用于 `source == 'dz'` 的 Discuz 老帖（表单语义与返回略有差异）。
本工具**固定走 `replyAdd` + `source=qiniu`**：抓包里所有帖子（含从 Discuz 迁移来的）用该组合
发帖/评论均成功。若将来遇到明确只认 `replyAddV1` 的帖子，可作为扩展点单独加分支
（届时 `reply_id` 语义需再核对）——当前不改。

### 前端落地（`webui/src/components/ThreadPanel.tsx`）

- 有 `to_comment.id` 的评论：正文上方渲染**引用块**——浅灰底 + 左侧竖线 + 小一号字，
  显示 `@user_name` 与消毒后的引用原文（样式对齐 WAP 的 `comment-feed`）。
- 每条评论头部有「回复」小按钮（与点赞数同区）：点击设 `replyTarget={id, user_name}`，
  输入框 placeholder 变为「回复 @name」，上方出现可关闭的 `<Tag>回复 @name</Tag>`（关闭即取消）；
  点其它评论的「回复」直接切换目标。
- 提交时若 `replyTarget` 非空 → `addComment(..., replyId)`（表单带 `reply_id`）；成功后清空输入与
  回复目标、回第 1 页刷新（与普通评论一致）。切换帖子时一并清空 `replyTarget`。
- 引用块整体**不做跳转**（目标评论可能不在当前页，跳转需额外定位逻辑，收益低）。
