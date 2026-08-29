# SDGun「水弹社区」网页版

基于 iOS App 抓包逆向的社区网页客户端（水弹社区 / SDGun，MAGAPPX 混合框架）。
含接口分析文档、本地 CORS 代理、React 管理界面与 TypeScript 类型定义。

## 文件清单

| 文件/目录 | 说明 |
|---|---|
| `API-ANALYSIS.md` | 接口逆向分析文档（7 份抓包的全部结论：鉴权、登录、搜索、写操作、WAF 指纹、频道流分页、用户主页、私信收发、评论、签到、外链证书坑等；开头有抓包索引表） |
| `sdgun-api-types.ts` | 全部接口的 TypeScript 类型定义（类型主文件，webui 内有同步副本，构建时自动校验一致） |
| `sdgun_proxy.py` | 代理应用层：FastAPI 路由、转发核心、`/link` 外链跳桥、`/health` 健康探针、静态托管 |
| `sdgun_session.py` | 会话层：上游身份头、线程安全会话存储、抓包解析、启动自检（并发探测） |
| `sdgun_sign.py` | 每日签到：状态解析、守护线程、`/sign` 路由 |
| `sdgun_messages.py` | 私信本地归档：收发消息落盘（`sdgun_messages.json`）、`/messages/local` 历史查询 |
| `sdgun_api_test.py` | 接口连通性测试脚本（8 个用例，依赖真实会话，属诊断工具） |
| `tests/` | 离线回归测试（pytest，不依赖网络；改动解析逻辑/升级依赖前必跑） |
| `webui/` | React 18 + Ant Design 5 前端（Vite + TS strict） |
| `sdgun-2026-08-2*.json` | 抓包文件（Anything Analyzer 导出；代理会自动发现并提取会话与登录记录） |
| `sdgun_session.json` | 代理自维护的会话（自动生成，含上游 Set-Cookie 吸收） |
| `sdgun_sign.json` | 自动签到状态（最近签到日期/结果；删掉无害，幂等） |
| `启动.bat` | Windows 一键启动（0.0.0.0:8000 并显示局域网地址） |
| `deploy/` | VPS Docker 部署（Dockerfile + requirements + deploy.py 一键脚本） |

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
python deploy/deploy.py    # 一键：上传 → 构建 → 运行（需改脚本里的主机/密码）
```

服务器上的布局与运维：

| 项 | 位置 |
|---|---|
| 代码（代理 + 前端构建产物） | `/opt/sdgun/app`（打进镜像） |
| 会话/抓包数据（挂载卷） | `/opt/sdgun/data` → 容器 `/data` |
| 端口 | 宿主机 `50002` → 容器 `8000` |
| 容器 | `docker restart sdgun`（`--restart unless-stopped` 开机自启） |
| 直连路由（绕过 xray0 隧道） | `systemctl status sdgun-upstream-routes` |

该 VPS 的网络要点（换环境部署时注意）：
1. 默认路由走 xray0 隧道，但上游（sdgun 系域名）必须走物理网关——
   已用**策略路由**（`from 172.17.0.0/16 table 100`，默认路由指向物理网关，
   含 docker0 回程路由）让容器流量整体直连，不受七牛 DNS 轮换 IP 影响；
2. 七牛 CDN 的 IPv6 边缘会断连——代理代码已强制 IPv4（urllib3
   allowed_gai_family 补丁），容器同时禁用了 IPv6。

更新会话/抓包：把新 JSON 放到 `/opt/sdgun/data/` 后 `docker restart sdgun`。
更新代码：改本地文件后重跑 `deploy/deploy.py`。
签到状态文件 `sdgun_sign.json` 也在 data 卷（删掉会重签检查一次，幂等无害）。

代理职责：CORS 放行、注入 App 身份头（UA/mag-version/X-Requested-With/Referer）、
会话 Cookie 管理、http 明文资源（聊天服务/图片）转发、图片防盗链代理
（`/proxy?url=`，白名单限 sdgun 相关域名，跟随重定向）、
**HTML 图片改写**（WAP 页/threadViewPage 里直连图床的 src/data-original
统一改走 `/proxy`，兼容 lazyload 与 var row JSON 转义形态）、
外链跳桥（`/link?url=`，绕开 e.tb.cn 等证书不匹配域名，不携带会话 Cookie）、
每日自动签到、`/health` 会话健康探针（60s 缓存，`?force=1` 强制刷新，
供 Docker HEALTHCHECK 与排障用）。

## 开发与维护

- **版本控制**：git 仓库（`sdgun*.json` 抓包/会话数据已在 `.gitignore` 排除，不会入库）。
  日常改动随手 commit，上游改版时可用历史 bisect 回溯。
- **回归测试**：`python -m pytest tests/`（全部离线）。覆盖 HTML 图片改写、
  var row 提取、抓包 Cookie 解析、会话存储并发、/proxy 白名单守卫等最易碎的
  解析逻辑。**升级 requests/urllib3/fastapi 前必须先跑测试**——
  上游 WAF 校验请求头指纹、IPv4 强制依赖 urllib3 私有 API，依赖版本已在
  `deploy/requirements.txt` 锁定，升级是有风险的决定而不是被动接受。
- **类型双副本**：根目录 `sdgun-api-types.ts` 与 `webui/src/types/` 内副本
  必须一致，`npm run build` 会自动校验，不一致直接构建失败。
- **双实现同步**：`var row` 提取（Python `sdgun_sign._extract_var_row` ↔ TS
  `media.ts extractThreadRow`）与 HTML 图片改写（`sdgun_proxy._rewrite_html_images`
  ↔ `media.ts prepareCommentHtml`）是同一算法两份实现，上游改格式时两处必须同步。

## 登录机制

- 官方登录 `POST /mag/user/v1/user/login` 的密码为 RSA 加密，公钥不外发，
  短信验证码通道实际不可用（发送接口不存在）。
- **已验证可行的方案**：完整重放——把抓包里登录请求原样重放（同密文 +
  同 `mag-ss-key` + 同 PHPSESSID Cookie），服务端接受并签发新 token。
  网页端「登录」按钮 → 代理 `POST /auth/replay-exact` 即此实现。
- 限制：改密码后需重新录一次登录抓包；若重放返回"密码错误"（失败 3 次锁定登录），
  重新录包恢复，勿连续点击。**密文重放有效期已实测 ≥ 数小时～1 天**
  （08-25 上午录的登录包，当天上午会话失效后重放成功换得新 token）。
  会话失效的常见诱因：App 内重新登录会使旧 token 失效——失效后网页点「登录」即可恢复。

## 会话管理与自愈

- 会话核心是三个 Cookie：`3df5d0fc98d8c119af2e389a3f45b5b0=<token>`（鉴权）、
  `PHPSESSID`、`server_session_*`，有效期约 41 天，可与手机 App 多点共存。
- 代理三层自愈：① 启动时自检候选会话（session.json → 抓包文件，新→旧，
  用 jsApi 校验挑活的）；② 运行中吸收上游 Set-Cookie 跟住 PHPSESSID 轮换；
  ③ 失效后网页一键「登录」重放换新。
- 前端本地 Cookie（「会话设置」粘贴）优先级最高，失效会自动降级为代理会话。

## 前端功能

- 信息流：频道/分类下拉（名称来自 index/pages + channelConfigById）、
  **频道流真实分页**（p=1 的 totalList 作游标，p>=2 传 content_id 切片；玩具交易区实测 25+ 页）、
  版块浏览（circleTree + threadList）
- 搜索：帖子（关键词高亮，可翻页）/ 用户（可关注、可点进主页），热搜词
- 帖子详情：楼主栏（头像/昵称可点击进主页）、正文（threadViewPage 内嵌 `var row` 提取）、
  图片代理与懒加载修复、**发表评论**（Enter 发送，成功后自动刷新）、评论分页（评论用户可点击进主页）、
  红包/悬赏查询、打赏记录、点赞、关注作者
- **帖内链接可点击**：站内链接走本站代理路径（WAP 页同源渲染），
  外链（如淘宝短链 e.tb.cn，https 证书不匹配）经 `/link` 跳桥路由绕开证书错误
- **用户主页**：头部资料（明文昵称走 loadUsers、用户组、粉丝/关注/访问数、最后登录）、
  动态 / 论坛帖子 / 资料（注册时间、积分、兴趣标签、等级）三个 tab、关注、私信入口
- **私信（可收发 + 本地历史 + 新到提醒）**：`/msg/sendMsg` 发送（sign 仅要求存在、
  不验内容）；代理对经过的每条消息（收到的 + 发出的）**落盘归档**（`sdgun_messages.json`
  在数据卷，上限 5000 条 FIFO），`GET /messages/local?peer_id=user<uid>` 查历史。
  前端全局监听每 15 秒轮询一次未读：新私信弹右下角通知 + 顶栏「私信」角标，
  打开会话面板自动载入归档历史。
  ⚠️ 上游限制：unReadMsg **拉取即清**（App 与网页共用消费池），App 先读走的消息
  代理归档不到；归档自 2026-08-29 功能上线起累积，此前历史上游无法找回
  （详见 API-ANALYSIS.md 附录 5/8）
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

- **上游 WAF 校验请求头指纹**：转发必须用 requests（httpx 的默认头序会被判
  "登录信息已过期"），详见 API-ANALYSIS.md 附录 3。
- 评论/正文为服务端富文本 HTML，当前直接渲染；公网部署前需接 DOMPurify 消毒。
- 付费帖正文服务端只下发部分内容。
- 代理默认携带抓包兜底会话（等于你的账号登录态），**不要暴露到公网**；
  `Access-Control-Allow-Origin: *` 为本地开发配置，正式部署需收紧。
- 真实写操作（点击即生效、他人可见）：点赞、关注、**评论**、私信、每日任务、签到；
  评论支持删除（响应里的 delete_url）。

## 依赖

- Python 3.10+：`fastapi`、`uvicorn`、`requests`（代理，版本已锁定见
  `deploy/requirements.txt`）；开发另需 `pytest`（跑 `tests/`）
- Node 18+：前端 `npm install`（react / antd / axios / vite / typescript，
  构建走 lockfile 保持可复现）
