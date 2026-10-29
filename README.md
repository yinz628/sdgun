# SDGun「水弹社区」网页版

基于 iOS App 抓包逆向的社区网页客户端（水弹社区 / SDGun，MAGAPPX 混合框架）。
含接口分析文档、本地 CORS 代理、React 管理界面与 TypeScript 类型定义。

## 文件清单

| 文件/目录 | 说明 |
|---|---|
| `API-ANALYSIS.md` | 接口逆向分析文档（8 份抓包 + 线上实测的全部结论：鉴权、登录、搜索、写操作、WAF 指纹、频道流分页、用户主页、私信收发、评论、签到、外链证书坑、502「帖子不存在」语义等；开头有抓包索引表） |
| `webui/src/types/sdgun-api-types.ts` | 全部接口的 TypeScript 类型定义（唯一源，前端直接引用；原根目录副本与构建期校验脚本已移除） |
| `sdgun_proxy.py` | 代理应用层：FastAPI 路由、转发核心、`/link` 外链跳桥、`/health` 健康探针、静态托管 |
| `sdgun_session.py` | 会话层：上游身份头、线程安全会话存储、抓包解析、启动自检（并发探测） |
| `sdgun_sign.py` | 每日签到：状态解析、守护线程、`/sign` 路由 |
| `sdgun_messages.py` | 私信本地归档：收发消息落盘（`sdgun_messages.json`）、`/messages/local` 历史查询、`/messages/conversations` 会话列表（按对端分组摘要） |
| `sdgun_favorites.py` | 帖子收藏（本地标记，可增删；`sdgun_favorites.json`） |
| `sdgun_likes.py` | 点赞本地归档（上游无「我的点赞」接口；`sdgun_likes.json`） |
| `sdgun_watch.py` | 关注关键字监控：代理侧守护线程轮询信息流，新帖标题命中规则即记录（`sdgun_watch.json`）+ `/watch/**` 路由 |
| `sdgun_api_test.py` | 接口连通性测试脚本（8 个用例，依赖真实会话，属诊断工具） |
| `tests/` | 离线回归测试（pytest，不依赖网络；改动解析逻辑/升级依赖前必跑） |
| `webui/` | React 18 + Ant Design 5 前端（Vite + TS strict） |
| `captures/sdgun-*.json` | 抓包文件（Anything Analyzer 导出；代理自动发现并提取会话与登录记录。旧平铺布局即放仓库/数据卷根目录仍兼容） |
| `sdgun_session.json` | 代理自维护的会话（自动生成，含上游 Set-Cookie 吸收） |
| `sdgun_sign.json` | 自动签到状态（最近签到日期/结果；删掉无害，幂等） |
| `sdgun_watch.json` | 关注关键字规则与命中记录（本地监控，删掉会重建基线，不回溯历史帖） |
| `启动.bat` | Windows 一键启动（0.0.0.0:8000 并显示局域网地址） |
| `deploy/` | VPS Docker 部署（`deploy_common.py` 公共层：凭据/容器参数单一来源 + `deploy.py` 全量 + `update_dist.py` 增量 + `monitor.py` 九项只读巡检） |

## 快速开始

### 日常使用（单程序模式，推荐）

前端已构建为静态文件由代理托管，**只需启动一个程序**：

```bash
cd F:\rchang\sdgun
python -m uvicorn sdgun_proxy:app --host 0.0.0.0 --port 8000
# 本机:   http://127.0.0.1:8000
# 局域网: http://<本机IP>:8000   （如 http://192.168.2.248:8000）
```

或直接双击 `启动.bat`（自动显示局域网地址）。

> - 局域网访问需 Windows 防火墙放行 8000 端口（已添加入站规则「SDGun Proxy 8000」；
>   换机器时需管理员执行：`netsh advfirewall firewall add rule name="SDGun Proxy 8000" dir=in action=allow protocol=TCP localport=8000`）
> - ⚠️ 局域网内任何设备都可使用页面，且共享代理里的登录态（等于你的账号），
>   仅在可信网络开放；`--host 0.0.0.0` 不要暴露到公网。
> - 前端有改动后需重新构建才会生效：`cd webui && npm run build`

### 开发模式（前端热更新）

```bash
# 终端 1：代理（同上）
python -m uvicorn sdgun_proxy:app --host 127.0.0.1 --port 8000
# 终端 2：vite dev（webui/.env.development 已指向 8000 代理）
cd webui && npm run dev   # http://localhost:5173
```

### Docker 部署到服务器（已部署至 192.168.2.197:50002）

```bash
# 凭据经环境变量传入（不再写死在脚本里，避免随仓库/备份/AI 工具外流）：
#   PowerShell:  $env:SDGUN_DEPLOY_PASSWORD='...'
#   Git Bash:    SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py
# 也可用私钥：SDGUN_DEPLOY_KEYFILE=~/.ssh/id_rsa；主机/用户可用
#   SDGUN_DEPLOY_HOST / SDGUN_DEPLOY_USER 覆盖（默认 192.168.2.197 / root）
SDGUN_DEPLOY_PASSWORD=... python deploy/update_dist.py   # 日常用这个（增量，不碰数据卷）
SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py        # 全量：会覆盖数据卷里的会话文件，慎用
SDGUN_DEPLOY_PASSWORD=... python deploy/monitor.py       # 部署后九项只读巡检（--wait N 复查健康态）
```

> ⚠️ **日常更新（改了后端 .py 或前端）一律用 `update_dist.py`**。`deploy.py` 会把本地所有
> `sdgun*.json`（含 `sdgun_session.json`）盖到服务器数据卷——本机的会话文件可能早已过期，
> 全量上传会把 VPS 上仍有效的会话覆盖成失效会话，整站变"登录信息已过期"。
> 只想给 VPS 换会话/补抓包时：把新 JSON 放到 `/opt/sdgun/data/captures/` 后 `docker restart sdgun`
> （抓包约定放数据卷的 `captures/` 子目录；旧平铺布局即直接放 `/opt/sdgun/data/` 仍兼容）。
>
> 三个脚本共用 `deploy_common.py`（SSH 连接、上传、`docker run` 参数的单一事实来源）；
> 容器参数（DNS、端口、挂载卷）只需改 `deploy_common.DOCKER_RUN` 一处。

服务器上的布局与运维：

| 项 | 位置 |
|---|---|
| 代码（代理 + 前端构建产物） | `/opt/sdgun/app`（打进镜像） |
| 会话/抓包数据（挂载卷） | `/opt/sdgun/data` → 容器 `/data` |
| 端口 | 宿主机 `50002` → 容器 `8000` |
| 容器 | `docker restart sdgun`（`--restart unless-stopped` 开机自启） |
| 直连路由（绕过 xray0 隧道） | `systemctl status sdgun-upstream-routes`（**必须 enabled+active**） |
| 路由自愈定时器 | `systemctl list-timers sdgun-routes-assert.timer`（每分钟幂等重申 ip rule + table 100 三条路由，2026-09-10 起） |

该 VPS 的网络要点（换环境部署时注意）：
1. 默认路由走 xray0 隧道，但上游（sdgun 系域名）必须走物理网关——
   已用**策略路由**（`from 172.17.0.0/16 table 100`，默认路由指向物理网关，
   含 docker0 回程路由）让容器流量整体直连，不受七牛 DNS 轮换 IP 影响；
   ⚠️ 2026-09-09 事故教训：该服务一度处于 inactive+disabled（ip rule 丢失，容器流量
   全走隧道），症状是**整站上游不可达**——启动自检所有候选 Cookie 全报"无登录态"、
   宿主机 `curl https://mag1.sdgun.net` 超时。恢复：`systemctl enable --now
   sdgun-upstream-routes`（已设开机自启）。排查上游故障时先看这里再看会话；
   ⚠️ 2026-09-10 第二课：table 100 的 `172.17.0.0/16 dev docker0` 回程路由会被
   不明进程删除（疑似 docker 事件），症状极具迷惑性——**局域网直连容器页面正常，
   但 docker-proxy 路径（127.0.0.1:50002 与健康检查）全部挂死**，因为容器回给
   宿主机 172.17.0.1 的包被丢到网关路由。已装 `sdgun-routes-assert.timer`
   （每分钟幂等重申 rule+路由）自愈；应急恢复命令：`ip route replace
   172.17.0.0/16 dev docker0 table 100`；
2. 七牛 CDN 的 IPv6 边缘会断连——代理代码已强制 IPv4（urllib3
   allowed_gai_family 补丁），容器同时禁用了 IPv6；
   ⚠️ 2026-09-10 第三课：容器禁 IPv6 后，宿主 resolv.conf 被 DHCP 重写时
   Docker 同步会把 IPv4 DNS 条目滤掉、只留下容器不可达的 IPv6 DNS →
   上游域名解析全挂（所有 /mag/** 与登录全 502，日志报 NameResolutionError）。
   根治：`docker run --dns 223.5.5.5 --dns 119.29.29.29`（两个部署脚本均已加，
   公共 DNS 走直连路由可达，与宿主解析解耦）。

更新会话/抓包：把新 JSON 放到 `/opt/sdgun/data/captures/` 后 `docker restart sdgun`
（抓包约定放数据卷的 `captures/` 子目录，代理优先扫描该目录；旧平铺布局即直接放
`/opt/sdgun/data/` 仍兼容）。
更新代码：改本地文件后重跑 `deploy/deploy.py`；只改前端则跑 `deploy/update_dist.py`。
签到状态文件 `sdgun_sign.json` 也在 data 卷（删掉会重签检查一次，幂等无害）。

代理职责：CORS 放行、注入 App 身份头（UA/mag-version/X-Requested-With/Referer）、
会话 Cookie 管理、http 明文资源（聊天服务/图片）转发、图片防盗链代理
（`/proxy?url=`，白名单限 sdgun 相关域名，跟随重定向）、
**HTML 图片改写**（WAP 页/threadViewPage 里直连图床的 src/data-original
统一改走 `/proxy`，兼容 lazyload 与 var row JSON 转义形态）、
**threadViewPage 服务端解析信封**（前端带 `?sdgun_parsed=1` 时：代理剥掉该参数转发、
照常改写 HTML，再服务端提取 var row 并代理化其中纯字段图片 URL，返回
`{parsed_row, html}` JSON；不带参数的请求仍原样透传 HTML，不影响帖内站内链接的同源渲染）、
外链跳桥（`/link?url=`，绕开 e.tb.cn 等证书不匹配域名，不携带会话 Cookie）、
每日自动签到、**关注关键字监控**（`/watch/**`，守护线程轮询信息流，命中即落本地）、
`/health` 会话健康探针（60s 缓存，`?force=1` 强制刷新，
供 Docker HEALTHCHECK 与排障用）。

## 访问控制

默认与原行为完全一致：**不设访问令牌、CORS 全开**，只适合本机/可信局域网。
代理携带论坛登录态，一旦要暴露到公网，请同时启用下面两项（都从代理进程的
环境变量读取，未设置即关闭/维持现状）。

### 访问令牌门禁 `SDGUN_ACCESS_TOKEN`

- 未设置或为空：门禁关闭（现状，无需任何前端改动）。
- 已设置：除 `/`、`/index.html`、`/assets/**`、`/favicon.ico`、`/health`、
  `/proxy-info` 外，所有请求必须携带令牌，否则返回
  `401 {"success": false, "code": 401, "msg": "未授权:需要访问令牌"}`。
  豁免项只做精确/前缀匹配，`/link`、`/proxy`、`/mag/**` 等动态路由一律需要令牌。
- 两种携带方式任选：`Authorization: Bearer <token>` 或 `X-Sdgun-Token: <token>`
  （`hmac.compare_digest` 比较，大小写敏感）。
- 前端（webui）在收到该 401 时会弹窗要求输入令牌，存入 localStorage（`sdgunToken`）
  后整页重载；**这是代理访问令牌，不是论坛账号密码/登录态**。
- 令牌与论坛登录无关：它只保护代理入口，不能替代论坛 Cookie。

### CORS 白名单 `SDGUN_CORS_ORIGINS`

- 未设置：维持 `Access-Control-Allow-Origin: *`（本地开发不变）。
- 已设置：逗号分隔的 origin 列表，如
  `https://a.example,http://127.0.0.1:5173`；仅当请求 `Origin` 命中时精确回显该
  origin，未命中/无 Origin 时**不回任何 CORS 头**。
- 单程序模式（页面由代理同源托管）本就不需要 CORS；通常是 vite 开发
  （`:5173`）或独立前端站点才需要配置。

### VPS 部署示例

本机设置环境变量后运行部署脚本，`deploy/deploy_common.py` 的 `DOCKER_RUN`
会自动把这两项以 `-e` 注入容器（未设置时不追加任何参数，命令与原来完全一致）：

```bash
SDGUN_ACCESS_TOKEN='足够随机的长令牌' \
SDGUN_CORS_ORIGINS='https://your-domain.example' \
SDGUN_DEPLOY_PASSWORD='...' \
python deploy/update_dist.py
```

> 注意：该令牌会出现在部署命令字符串与 `docker inspect` 的容器环境变量里，
> 请用足够随机的值，并限制部署终端的访问。

## 开发与维护

- **版本控制**：git 仓库（`sdgun*.json` 抓包/会话数据已在 `.gitignore` 排除，不会入库）。
  日常改动随手 commit，上游改版时可用历史 bisect 回溯。
- **回归测试**：`python -m pytest tests/`（全部离线）。覆盖 HTML 图片改写、
  var row 提取与 threadViewPage 服务端解析信封、抓包 Cookie 解析、会话存储并发、
  /proxy 白名单守卫、
  /proxy 媒体流式（含 chunked 无 content-length 回归）、/link 跳桥重定向分流、
  关注关键字匹配与基线/裁剪语义等最易碎的解析与转发逻辑。**升级 requests/urllib3/fastapi 前必须先跑测试**——
  上游 WAF 校验请求头指纹、IPv4 强制依赖 urllib3 私有 API，依赖版本已在
  `deploy/requirements.txt` 锁定，升级是有风险的决定而不是被动接受。
- **类型定义**：唯一源为 `webui/src/types/sdgun-api-types.ts`（原根目录副本
  `sdgun-api-types.ts` 与构建期一致性校验脚本 `scripts/check-types-sync.mjs`
  已于 2026-10-29 移除，前端直接引用该文件）。
- **解析单源化与前端回落（阶段5 双路过渡）**：
  - `var row` 提取**已单源化**到后端 `sdgun_parse.extract_var_row`（`sdgun_sign` 与
    `sdgun_proxy` 的 threadViewPage 信封共用同一实现）；前端 `media.ts extractThreadRow`
    仅作**过渡回落**——`?sdgun_parsed=1` 信封不可用或 `parsed_row=null` 时使用，
    待观察期后由独立任务移除。
  - HTML 图片改写：**页面侧已服务端化**（`sdgun_proxy._rewrite_html_images` 改写 WAP 页/
    threadViewPage，信封内再以 `proxify_images` 代理化 var row 的纯字段图片 URL）；
    评论富文本侧仍由前端 `media.ts prepareCommentHtml` 承担（与 DOMPurify 消毒同管线），
    **有意保留**，不随本次单源化移除。

## 登录机制

- **账号密码登录（主路，2026-09-09 发现并实测可用）**：WAP 站 Vue 登录页走的是大写 `User` 的
  `POST /mag/user/v1/user/login`——**表单明文**，无 RSA、无 mag-ss-key，与 App 原生通道
  （小写 `user/login`，RSA 密文 + mag-ss-key，公钥不外发）完全不同。
  网页「登录」按钮 → 弹窗输入账号密码 → 代理 `POST /auth/login` 代为提交并接管会话
  （连接层失败自动重试一次；请求未达服务器，不会计入上游失败计数）。
  改密码后直接可用，无需重录抓包。详见 API-ANALYSIS.md 附录 10。
  ⚠️ 上游失败 3 次临时锁定登录，失败提示中的计数请留意；密码经本机代理明文转发，
  仅限本地/可信局域网使用。
- **重放登录（备用）**：完整重放抓包里的登录请求（同密文 + 同 `mag-ss-key` + 同 PHPSESSID），
  代理 `POST /auth/replay-exact`；登录弹窗与「会话设置」里均保留该入口（免输密码）。
  限制：改密码后需重录登录抓包；密文重放有效期实测 ≥ 数小时～1 天。
- 短信验证码通道实际不可用（发送接口不存在）。

## 会话管理与自愈

- 会话核心是三个 Cookie：`3df5d0fc98d8c119af2e389a3f45b5b0=<token>`（鉴权）、
  `PHPSESSID`、`server_session_*`，标称有效期约 41 天，可与手机 App 多点共存；
  但上游会**不定时清理 token**（疑似 App 端活动触发，2026-09-09 实测：当天新鲜
  抓包派生的会话也在十几分钟内被清）——不要指望抓包会话长期可用，
  失效后走网页账号密码登录即换新。
- 代理三层自愈：① 启动时自检候选会话（session.json → 抓包文件，新→旧，
  用 jsApi 校验挑活的）；② 运行中吸收上游 Set-Cookie 跟住 PHPSESSID 轮换；
  ③ 失效后网页「登录」输账号密码即换新（备用：重放最新抓包登录）。
- 前端本地 Cookie（「会话设置」粘贴）优先级最高，失效会自动降级为代理会话。

## 前端功能

- 信息流：频道/分类下拉（名称来自 index/pages + channelConfigById）、
  **频道流真实分页**（p=1 的 totalList 作游标，p>=2 传 content_id 切片；玩具交易区实测 25+ 页）、
  版块浏览（circleTree + threadList）
- 搜索：帖子（关键词高亮，可翻页）/ 用户（可关注、可点进主页），热搜词
- 帖子详情：楼主栏（头像/昵称可点击进主页）、正文（threadViewPage 内嵌 `var row` 提取）、
  图片代理与懒加载修复、**发表评论**（点击「发表评论」按钮发送，Enter 仅换行，成功后自动刷新）、
  **楼中楼引用与定向回复**（被回复评论显示 `@昵称` + 引用原文；每条评论「回复」按钮设目标后
  发送即带 `reply_id`，可关闭 Tag 取消）、评论分页（评论用户可点击进主页）、
  红包/悬赏查询、打赏记录、点赞、关注作者。详见 API-ANALYSIS 附录 12。
- **帖内链接可点击**：站内链接走本站代理路径（WAP 页同源渲染），
  外链（如淘宝短链 e.tb.cn，https 证书不匹配）经 `/link` 跳桥路由绕开证书错误
- **用户主页**：头部资料（明文昵称走 loadUsers、用户组、粉丝/关注/访问数、最后登录）、
  动态 / 论坛帖子 / 资料（注册时间、积分、兴趣标签、等级）三个 tab、关注、私信入口
- **私信（可收发 + 本地历史 + 会话列表 + 新到提醒）**：`/msg/sendMsg` 发送（sign 仅要求存在、
  不验内容）；代理对经过的每条消息（收到的 + 发出的）**落盘归档**（`sdgun_messages.json`
  在数据卷，上限 5000 条 FIFO），`GET /messages/local?peer_id=user<uid>` 查历史。
  前端全局监听每 15 秒轮询一次未读：新私信弹右下角通知（**点击直达对话**）+ 顶栏「私信」角标；
  顶栏「私信」按钮打开**会话列表抽屉**（`GET /messages/conversations` 本地归档按对端分组：
  昵称/最后消息/条数/未读点），点击某会话直接进对方主页并自动展开聊天面板，
  不用再从用户搜索绕路（2026-09-10）。
  ⚠️ 上游限制：unReadMsg **拉取即清**（App 与网页共用消费池），App 先读走的消息
  代理归档不到；归档自 2026-08-29 功能上线起累积，此前历史上游无法找回
  （详见 API-ANALYSIS.md 附录 5/8）
- **通知中心（回复/@/赞/关注提醒，2026-09-10）**：登录后每 60 秒轮询
  `userNotifyIndex`（实测轮询不清零计数）；出现新的回复/提到/点赞/关注时右下角弹提醒
  + 顶栏「通知」角标。点「通知」打开面板拉取各分类明细（`notifyList?code=`，
  含帖子标题与跳转 link），点击条目直达对应帖子。已读状态存浏览器
  localStorage（上游无清除接口，计数由本地管理）。
- **关注关键字新帖提醒（本地监控，2026-10）**：顶栏「关注」维护一份**关注规则**
  （每条规则一个或多个关键字；**同一规则内需全部命中（AND）**，**多条规则任一命中即提醒（OR）**，
  大小写不敏感，匹配帖子标题）。匹配与轮询在**代理侧守护线程**完成（页面不开也有效，
  默认每 60 秒一次），新出现的帖子命中即记入 `sdgun_watch.json`（数据卷，命中 ≤1000 条、
  已见 id ≤30000 条 FIFO 裁剪，并对命中按 tid 兜底去重——见下）；前端每 30 秒拉一次命中记录，弹右下角通知 + 顶栏角标，
  点击直达帖子；抽屉里可管理规则、看最近命中与监控状态、手动「立即检测」。
  - 命中列表按**最新在上**展示，并按**未读/已读**分类（已读状态存浏览器本地
    `sdgunWatchReadTids`，**按帖子 tid** 记录、上限 2000 条只留最新——同一帖子会因去重/
    裁剪/重现换 hit_id，所以不能按 hit_id 记；旧版本按 hit_id 存的数据会自动迁移）：
    点开某条即记为已读，未读分类头部有「全部已读」；顶栏角标 = 未读命中数，刷新页面不会清零。
  - **提醒时间窗**：只提醒「发布时间在 N 小时内」的帖子（12/24/48 可选，默认 24，
    抽屉里切换，服务端设置），用来压掉「旧帖被算法翻出」的误报。发布时间取版块源的
    `create_time`（精确）；条目没有该字段时放行（宁可多提醒也不漏帖）。
  - **一切以「发帖时间」为准**：版块列表 `threadList` 实为「最后活动置顶」排序（昨天发的帖
    因有新回复会排到 2 小时前的帖上面），所以命中列表按**发帖时间**倒序展示，并且只提醒
    **发布晚于规则创建时间**的帖子（规则「从创建那一刻起」盯新帖，与基线不回溯一致）。
    没有 `create_time` 的频道源条目两道门槛都放行。
  - **保留策略**：命中记录 1000 条 FIFO（`MAX_HITS`）；已见 id 30000 条（`MAX_SEEN_IDS`，
    须覆盖「最长窗口 48h × 峰值发帖速率」，实测交易区约 100+ 帖/小时）。另按 **tid 兜底去重**：
    只要该帖的命中记录还在保留期内就不再提醒——即使已见记录被裁剪或因换源被重置，
    被回帖翻起的老帖也不会重复提醒。
  - 监控源：自动解析首页框架里第一个频道（`column_type=7`，实测「玩具交易区」）里
    `cat_name` 含「新帖」的分类（找不到才用第一个），用 v2 频道流的 `totalList` 过滤广告位。
    ⚠️ **频道分类流是策展视图，会漏新帖**（2026-10-22 实测：玩具交易区 7/11 分钟前的新帖
    不在频道流首页 50 条里，版块列表 `threadList?fid=176` 才有）——交易类关键字请用
    `SDGUN_WATCH_FID=<版块 fid>` 指定**版块源**（优先级最高，条目字段名不同但已归一化）；
    也可用 `SDGUN_WATCH_CHANNEL_ID` + `SDGUN_WATCH_CAT_ID`（两者都设）指定频道分类。
    换源会自动重建基线（规则与命中历史保留），不会把新源里的旧帖当新帖弹出来。
  - 环境变量：`SDGUN_WATCH=0` 关闭守护线程；`SDGUN_WATCH_INTERVAL=秒` 改轮询间隔
    （夹在 20..3600，0 也表示关闭）；`SDGUN_WATCH_FID=176` 指定版块源（推荐交易类关键字）；
    `SDGUN_WATCH_CHANNEL_ID` + `SDGUN_WATCH_CAT_ID` 指定频道分类源。
  - ⚠️ **基线语义**：首次轮询只把当前信息流记入已见集合（建基线），**不产生命中**——
    命中自部署起累积，不回溯历史帖。已知限制：上游算法若把旧帖重新翻到信息流前列，
    会被当成新帖误报（信息流只有相对时间文案，没有可信的发布时间序）。
  - ⚠️ 监控用的是代理当前会话，会话失效期间轮询会失败（面板脚注显示「上次检测失败」），
    重新登录后自动恢复。
- **每日签到（自动）**：代理守护线程每日 08:05（北京时间）自动签到并落盘状态，
  服务重启自动补签当天；前端卡片展示今日状态/连续天数，可手动立即签到；
  可用环境变量 `SDGUN_AUTO_SIGN=0`（关闭）、`SDGUN_SIGN_TIME=HH:MM`（改时刻）配置
- 每日任务上报、聊天 token 换取与未读查询、用户信息（jsApi）

## 帖内视频说明

- 视频文件(`picapp.sdgun.net/video/*.mp4`)经 `/proxy` **流式转发**:透传浏览器的
  Range 头(206/Content-Range),起播快、可拖进度条,服务端不缓冲整文件。
- **部分视频是 HEVC(H.265)编码**(上游原始文件如此)。桌面 Chrome/Edge 需要系统
  HEVC 解码支持才能显示画面,否则"只有声音没有画面"。前端会自动检测:不支持时在
  视频下方显示提示条,提供「下载原片」链接(本地播放器如 PotPlayer 均可播)。
  想在浏览器里直接看:Windows 设置 → 应用 → 可选功能 → 添加「HEVC 视频扩展」
  (或微软商店安装,部分机型免费)。
- 不做服务端转码:VPS 仅 2 vCPU,HEVC 软解 + H.264 编码达不到实时,还会拖垮代理。
- 视频封面 poster 与图片同链路自动改写为代理地址(无黑框)。

## 已知限制与注意事项

- **上游把「帖子不存在/不可见」统一报成 `code=502「您的网络有些问题」`**（JSON 接口
  与正文页错误页同一文案）。列表里偶见的「0 阅读、无正文」帖即是：发表后很快被删除、
  审核中或其它不可见状态——feed 是发布瞬间抓到的，点击时帖子已不可见。
  该文案与网络无关，前端已对 code=502 做文案纠偏（详见 API-ANALYSIS.md 附录 9）。
- **上游 WAF 校验请求头指纹**：转发必须用 requests（httpx 的默认头序会被判
  "登录信息已过期"），详见 API-ANALYSIS.md 附录 3。
- 评论/正文为服务端富文本 HTML，来自上游社区用户——渲染前统一过 DOMPurify 消毒
  （`webui/src/utils/sanitize.ts`，经 `media.ts renderRichText` 管道：先代理改写再消毒，
  剥除 script/事件属性/javascript: 链接等；用户头衔与搜索标题同样过闸）。
- 付费帖正文服务端只下发部分内容。
- 代理默认携带抓包兜底会话（等于你的账号登录态），**不要暴露到公网**；
  公网部署前必须启用访问令牌门禁并收紧 CORS（用法见「访问控制」小节，
  未配置时与本地开发行为完全一致）。
- 真实写操作（点击即生效、他人可见）：点赞、关注、**评论**、私信、每日任务、签到；
  评论支持删除（响应里的 delete_url）。

## 依赖

- Python 3.10+：`fastapi`、`uvicorn`、`requests`（代理，版本已锁定见
  `deploy/requirements.txt`）；开发另需 `pytest`（跑 `tests/`）
- Node 18+：前端 `npm install`（react / antd / axios / vite / typescript，
  构建走 lockfile 保持可复现）
