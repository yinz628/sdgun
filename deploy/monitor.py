# -*- coding: utf-8 -*-
"""SDGun VPS 部署后监控脚本。

在本地运行,通过 SSH 到 VPS 执行一组只读检查项(不做任何写操作):
  1. 容器状态(Docker HEALTHCHECK 的 health 字段)
  2. /health?force=1     代理会话实时校验(真实调上游 jsApi)
  3. /health 缓存行为    连续两次调用的 checked_at 应相同(60s TTL 生效)
  4. 端到端 API 转发      走代理拉 /mag/index 配置(验证 WAF 请求头指纹仍被放行)
  5. 鉴权接口            jsApi 返回 _USER(会话在 VPS 上有效)
  6. HTML 图片改写       threadViewPage 里 /proxy?url= 改写计数
  7. 签到面板            /sign/status 实时上游状态
  8. 前端托管            首页 HTTP 200
  9. 错误日志扫描        docker logs 里的 error/warning/traceback

用法:python deploy/monitor.py [--wait N]  # --wait: 额外等 N 秒后复查容器健康态
凭据经环境变量(SDGUN_DEPLOY_PASSWORD / SDGUN_DEPLOY_KEYFILE,见 deploy_common)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deploy_common as dc

BASE = "http://127.0.0.1:50002"


def main():
    wait_after = 0
    if "--wait" in sys.argv:
        wait_after = int(sys.argv[sys.argv.index("--wait") + 1])

    ssh = dc.connect()

    def run(cmd: str, timeout: int = 60) -> tuple[str, str]:
        _, out, err = ssh.exec_command(cmd, timeout=timeout)
        return (out.read().decode("utf-8", "replace").strip(),
                err.read().decode("utf-8", "replace").strip())

    def curl(path: str, extra: str = "") -> str:
        return run(f"curl -s --max-time 30 '{BASE}{path}' {extra}")[0]

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  → {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print(f"===== SDGun VPS 监控检查 @ {dc.HOST}:50002 =====")

    # 1. 容器状态
    status = run("docker ps --filter name=sdgun --format '{{.Status}}'")[0]
    check("容器运行", "Up" in status, status)

    # 2. 会话实时校验
    h = curl("/health?force=1")
    check("会话有效(/health force)", '"session_ok":true' in h, h[:120])

    # 3. /health 60s 缓存生效(两次调用 checked_at 相同)
    a, b = curl("/health"), curl("/health")
    import json as _json
    try:
        same = _json.loads(a)["checked_at"] == _json.loads(b)["checked_at"]
    except (ValueError, KeyError):
        same = False
    check("健康探针 60s 缓存生效", same)

    # 4. 端到端 API 转发(WAF 指纹)
    cfg = curl("/mag/index/v2/index/config")
    check("API 转发(WAF 指纹放行)", '"success":true' in cfg or '"success": true' in cfg,
          cfg[:80])

    # 5. 鉴权接口带会话
    js = curl("/mag/index/v1/Index/jsApi")
    check("会话鉴权接口(jsApi)", "_USER" in js, js[:80])

    # 6. HTML 图片改写
    count = run(f"curl -s --max-time 30 '{BASE}/mag/circle/v1/forum/threadViewPage"
                f"?tid=4231648&mag_hide_progress=1' | grep -o '/proxy?url=' | wc -l")[0]
    check("HTML 图片改写生效", count.strip().isdigit() and int(count) > 0, f"/proxy?url= × {count}")

    # 7. 签到面板
    sign = curl("/sign/status")
    check("签到面板(/sign/status)", '"today"' in sign, sign[:100])

    # 8. 前端托管
    idx = run(f"curl -s -o /dev/null -w '%{{http_code}} %{{size_download}}B' --max-time 10 {BASE}/")[0]
    check("前端托管(首页)", idx.startswith("200"), idx)

    # 9. 错误日志
    logs = run("docker logs sdgun 2>&1 | grep -iE 'error|warning|traceback|exception' | tail -8")[0]
    check("无错误日志", not logs, (logs[:200] + "…") if logs else "干净")

    # 可选:等待 Docker HEALTHCHECK 周期后复查健康态
    if wait_after > 0:
        print(f"-- 等待 {wait_after}s 后复查 Docker 健康态 --")
        time.sleep(wait_after)
        status2 = run("docker ps --filter name=sdgun --format '{{.Status}}'")[0]
        check("Docker HEALTHCHECK 转 healthy", "healthy" in status2, status2)

    ssh.close()
    print(f"===== 结果: {len(failures)} 项失败 =====" if failures else "===== 全部通过 =====")
    if failures:
        print("失败项:", ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
