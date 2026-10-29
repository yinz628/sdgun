# -*- coding: utf-8 -*-
"""SDGun Docker 全量部署:本机 → VPS(连接凭据经环境变量,见 deploy_common)。

⚠️ 全量 = 会把本地所有 sdgun*.json(含 sdgun_session.json)上传到数据卷。
本机会话文件可能早已过期,会把 VPS 上仍有效的会话盖掉,整站变"登录信息
已过期"。日常更新一律用 update_dist.py;本脚本仅首次部署或确认要整体
重置(含换会话数据)时使用。

用法:
  SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deploy_common as dc


def main():
    ssh = dc.connect()

    # ---- 2. 环境检查 ----
    print("[2] 环境检查")
    dc.run(ssh, "cat /etc/os-release | head -2")
    docker_ok = dc.run(ssh, "docker --version", check=False, quiet=True)
    if docker_ok:
        print(f"    Docker 已安装: {docker_ok}")
    else:
        print("    未安装 Docker,开始安装(get.docker.com)...")
        dc.run(ssh, "curl -fsSL https://get.docker.com | sh", timeout=600)
        dc.run(ssh, "systemctl enable --now docker", timeout=60)
        print("    Docker 安装完成:", dc.run(ssh, "docker --version", quiet=True))

    # ---- 3. 上传文件 ----
    print("[3] 上传文件")
    dc.run(ssh, f"mkdir -p {dc.REMOTE_APP} {dc.REMOTE_DATA}", quiet=True)
    dc.upload_app_code(ssh)
    n = dc.upload_tree(ssh, os.path.join(dc.LOCAL_ROOT, "webui", "dist"),
                       f"{dc.REMOTE_APP}/webui/dist")
    print(f"    前端构建产物 {n} 个文件")
    for f in os.listdir(dc.LOCAL_ROOT):
        if f.startswith("sdgun") and f.endswith(".json"):
            dc.upload(ssh, os.path.join(dc.LOCAL_ROOT, f), f"{dc.REMOTE_DATA}/{f}")
            print(f"    数据文件 {f}")

    # ---- 4/5. 构建并换容器 ----
    print("[4] 构建镜像")
    dc.build_image(ssh)
    print("[5] 运行容器")
    dc.restart_container(ssh)
    print("[6] 启动日志")
    dc.run(ssh, "docker logs sdgun 2>&1 | head -8")

    # ---- 7. 容器内自检 ----
    print("[7] 服务自检(VPS 本机)")
    info = dc.run(ssh, "curl -s --max-time 10 http://127.0.0.1:50002/proxy-info", check=False)
    print("    proxy-info OK" if '"proxy"' in info else f"    异常: {info[:200]}")

    ssh.close()
    print(f"\n部署完成:http://{dc.HOST}:50002")


if __name__ == "__main__":
    main()
