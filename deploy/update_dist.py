# -*- coding: utf-8 -*-
"""SDGun 增量部署:本机 → VPS,更新代码(.py)+前端 dist 并重建容器。

与 deploy.py(全量)的区别:**绝不上传/触碰本地 sdgun*.json 与 /opt/sdgun/data**。
本地抓包/session 文件可能过期(如本机 sdgun_session.json 已失效),全量上传会把
VPS 上仍有效的会话文件盖掉,导致整站"登录信息已过期"。日常改了后端 .py 或前端
都可用本脚本,秒~分钟级完成、会话零风险(VPS 会话数据卷原样保留)。

用法(凭据经环境变量,见 deploy_common):
  SDGUN_DEPLOY_PASSWORD=... python deploy/update_dist.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deploy_common as dc


def main():
    ssh = dc.connect()

    print("[2] 上传后端代码(旧容器继续服务,不受影响)")
    dc.upload_app_code(ssh)

    print("[3] 上传 webui/dist")
    dc.run(ssh, f"rm -rf {dc.REMOTE_APP}/webui/dist && mkdir -p {dc.REMOTE_APP}/webui/dist")
    count = dc.upload_tree(ssh, os.path.join(dc.LOCAL_ROOT, "webui", "dist"),
                           f"{dc.REMOTE_APP}/webui/dist")
    print(f"    已上传 {count} 个文件")

    print("[4] 构建镜像")
    dc.build_image(ssh)

    print("[5] 换容器(数据卷不动;参数见 deploy_common.DOCKER_RUN)")
    dc.restart_container(ssh)

    print("[6] 自检")
    dc.run(ssh, "sleep 3 && curl -s --max-time 10 http://127.0.0.1:50002/health")
    dc.run(ssh, "curl -s --max-time 10 http://127.0.0.1:50002/ | grep -o 'assets/index[^\"]*\\.js' | head -1")
    # 空账号密码应在代理层被 400 拦下(有回归测试锁定),验证 auth 路由活着
    dc.run(ssh, "curl -s --max-time 10 -X POST http://127.0.0.1:50002/auth/login "
                "-H 'Content-Type: application/json' -d '{\"account\":\"\",\"password\":\"\"}'")

    ssh.close()
    print(f"\n增量部署完成(代码+前端,会话数据未动):http://{dc.HOST}:50002")


if __name__ == "__main__":
    main()
