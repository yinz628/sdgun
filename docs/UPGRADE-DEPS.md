# 依赖升级验证 runbook

本文件规定 SDGun 代理依赖升级的标准流程。**升级是有风险的决定,而不是被动接受**
——README「开发与维护」段落原话:

> **升级 requests/urllib3/fastapi 前必须先跑测试**——上游 WAF 校验请求头指纹、
> IPv4 强制依赖 urllib3 私有 API,依赖版本已在 `deploy/requirements.txt` 锁定,
> 升级是有风险的决定而不是被动接受。

原因(`deploy/requirements.txt` 头部注释同口径):

1. **WAF 请求头指纹**:上游校验请求头顺序等指纹,`requests` 的头序可过,
   换版本/换库(httpx 等)会被判「登录信息已过期」。
2. **urllib3 私有 API**:`sdgun_session.py` 用 `urllib3.util.connection.allowed_gai_family`
   补丁强制 IPv4(七牛 CDN 的 IPv6 边缘会断连),该 API 未承诺稳定。

任一条被破坏,症状都是**整站静默失效**(登录信息已过期 / 上游不可达),很难从表面定位。
因此升级必须走完整验证流程,且**绝不能用 `deploy.py` 全量部署做验证**(它会覆盖 VPS
数据卷上的会话文件,把一次依赖验证变成一次会话事故)。

---

## 0. 当前锁定版本(升级前先记录)

`deploy/requirements.txt` 精确锁定:

| 包 | 锁定版本 | 作用 |
| --- | --- | --- |
| fastapi | 0.115.12 | 代理 Web 框架 |
| uvicorn | 0.34.0 | ASGI 服务器 |
| requests | 2.32.5 | 上游 HTTP(WAF 头序;显式 Cookie 头优先) |
| urllib3 | 随 requests 传递依赖 | `allowed_gai_family` IPv4 补丁(未显式锁定,见下) |

`urllib3` 未在 requirements 里单独 pin,由 `requests` 约束(`urllib3<3,>=1.21.1`)。
升级前**必须记录实际解析到的版本**,否则回退时无法复现:

```bash
python -c "import fastapi, uvicorn, requests, urllib3; \
print(fastapi.__version__, uvicorn.__version__, requests.__version__, urllib3.__version__)"
# 或整份冻结:
python -m pip freeze > /tmp/sdgun-deps-before.txt
```

把候选版本与「当前版本」一起记进本次升级的说明(commit / 工单)里。

## 1. 在 venv 中安装候选版本

**绝不直接改系统 Python**,用独立 venv,便于一键丢弃:

```bash
python -m venv .venv-upgrade
. .venv-upgrade/Scripts/activate      # Git Bash;PowerShell: .venv-upgrade\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r deploy/requirements.txt        # 先装锁定基线
python -m pip install -U requests urllib3 fastapi uvicorn   # 再升到候选版本
python -m pip install pytest
```

## 2. 回归测试必须全绿

```bash
python -m pytest tests/ -q
```

要求:**0 失败**(当前基线 ≥145 通过;若测试数有变化以实际为准)。
任何失败 = 升级被拒,直接进第 7 步回退,不要带着红灯继续。

测试全部离线(monkeypatch 注入假数据,不发真实网络),因此失败一定是**代码/依赖
行为差异**,而不是网络抖动——这正是它作为门禁的价值。

## 3. 本地起代理 + 手动冒烟

自动化测试覆盖不到真实 WAF 指纹与真实会话,必须本地起代理做端到端冒烟:

```bash
python -m uvicorn sdgun_proxy:app --host 127.0.0.1 --port 8000
```

依次冒烟(任一失败即停,进第 7 步):

1. **登录**:`POST /auth/login {"account": "...", "password": "..."}` 返回 `success:true`
   (⚠️ 上游失败 3 次临时锁定登录,别反复试;也可用已有效会话跳过)。
2. **会话探活**:`GET /health?force=1` 返回 `"session_ok":true`(真实调上游 jsApi,
   验证 WAF 头指纹与 Cookie 头序仍被放行)。
3. **信息流**:`GET /mag/index/v2/index/pages` 与频道/版块流返回 JSON 正常
   (验证转发链路)。
4. **帖子详情**:`GET /mag/circle/v1/forum/threadViewPage?tid=<真实tid>&mag_hide_progress=1`
   返回 HTML 且图床 URL 已改写为 `/proxy?url=`(验证 HTML 图片改写)。

## 4. 增量部署到 VPS 验证

**只允许用 `update_dist.py`(增量:代码 + 前端,不碰数据卷)。**

```bash
SDGUN_DEPLOY_PASSWORD=... python deploy/update_dist.py
```

> ⚠️ **绝不用 `deploy.py` 全量做验证**:它会上传本地所有 `sdgun*.json` 覆盖 VPS
> 数据卷,把 VPS 上仍有效的会话盖成过期。`deploy.py` 现在有交互式确认(需输入 y),
> 但仍不该用于依赖验证——依赖验证用增量部署即可,会话数据零风险。

## 5. VPS 巡检(9 项)

```bash
SDGUN_DEPLOY_PASSWORD=... python deploy/monitor.py            # 可选 --wait N 复查健康态
```

9 项只读检查:容器状态、`/health?force=1` 会话实时校验、`/health` 60s 缓存、
端到端 API 转发(WAF 指纹)、jsApi 鉴权、HTML 图片改写、签到面板、前端托管、
错误日志扫描。**要求「全部通过」(0 项失败)**。

## 6. 固化版本

全部通过后,把候选版本写回 `deploy/requirements.txt`(精确 pin),连同新的
urllib3 实际版本一并锁定,再正常 commit。requirements 是唯一事实来源,Dockerfile
`pip install -r requirements.txt` 与 VPS 重建都读它。

## 7. 失败回退

任一门禁失败:

```bash
# 1) 改回 requirements 锁定的旧版本(直接用 git 恢复 requirements.txt)
git checkout -- deploy/requirements.txt
# 2) 在 venv 里装回锁定版本(或直接删掉 .venv-upgrade 重建)
python -m pip install -r deploy/requirements.txt
# 3) 用增量部署把 VPS 打回旧依赖(重建镜像 → 容器)
SDGUN_DEPLOY_PASSWORD=... python deploy/update_dist.py
# 4) 巡检确认已恢复
SDGUN_DEPLOY_PASSWORD=... python deploy/monitor.py
```

因为升级验证全程走 `update_dist.py`(数据卷不动),回退只影响代码/镜像层,
**会话数据始终安全**。

---

### 一句话总结

记录锁定版本 → venv 装候选 → `pytest tests/` 全绿 → 本地冒烟(登录/信息流/帖子详情)
→ `update_dist.py` 增量上 VPS(**绝不用 `deploy.py` 全量**)→ `monitor.py` 9 项通过
→ 失败就改回 requirements + 增量重部署。升级是主动决策,不是被动接受。
