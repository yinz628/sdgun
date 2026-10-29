# -*- coding: utf-8 -*-
"""SDGun Docker 全量部署:本机 → VPS(连接凭据经环境变量,见 deploy_common)。

⚠️ 全量 = 会把本地所有 sdgun*.json(含 sdgun_session.json)上传到数据卷。
抓包类(sdgun-*.json)上传到数据卷的 captures/ 子目录(与 sdgun_session.capture_files
的约定布局一致);会话/签到等运行时状态文件仍放数据卷根目录。
本机会话文件可能早已过期,会把 VPS 上仍有效的会话盖掉,整站变"登录信息
已过期"。日常更新一律用 update_dist.py;本脚本仅首次部署或确认要整体
重置(含换会话数据)时使用。

用法:
  SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py
  SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py --yes   # 自动化:跳过数据覆盖确认
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deploy_common as dc


def _data_files() -> list[tuple[str, str]]:
    """本机待上传的数据文件 → (本地路径, 远端目标路径),清单稳定。

    抓包类(sdgun-*.json,约定放本地 captures/)上传到数据卷 captures/ 子目录;
    会话/签到等运行时状态文件(sdgun_session.json / sdgun_sign.json)仍放数据卷根目录。
    根目录与 captures/ 两处都扫描:未迁移的旧布局(抓包堆在仓库根)也能被带上。
    """
    out: list[tuple[str, str]] = []

    def add(local_dir: str, name: str) -> None:
        # sdgun-*.json(带短横线)视为抓包,其余(sdgun_session.json 等下划线命名)为运行时状态
        remote_dir = f"{dc.REMOTE_DATA}/captures" if name.startswith("sdgun-") else dc.REMOTE_DATA
        out.append((os.path.join(local_dir, name), f"{remote_dir}/{name}"))

    for name in sorted(os.listdir(dc.LOCAL_ROOT)):
        if name.startswith("sdgun") and name.endswith(".json"):
            add(dc.LOCAL_ROOT, name)
    captures_dir = os.path.join(dc.LOCAL_ROOT, "captures")
    if os.path.isdir(captures_dir):
        for name in sorted(os.listdir(captures_dir)):
            if name.startswith("sdgun") and name.endswith(".json"):
                add(captures_dir, name)
    return out


def _confirm_data_overwrite(files: list[str]) -> None:
    """全量部署前交互确认:这些文件会覆盖 VPS 数据卷上的同名文件。

    VPS 上仍有效的会话文件一旦被本机(可能已过期)的同名文件覆盖,整站会变
    "登录信息已过期"。日常更新一律用 update_dist.py(不碰数据卷);只有首次部署
    或确认要整体重置(含换会话数据)时才继续。--yes 供自动化跳过。
    """
    print("[3a] ⚠ 即将上传以下数据文件覆盖 VPS 数据卷(可能把有效会话盖成过期):")
    for f in files:
        print(f"      {f}")
    print("      VPS 上仍有效的会话会被本机(可能过期的)文件覆盖,"
          "整站可能变「登录信息已过期」。")
    try:
        reply = input("      确认覆盖请输入 y 继续(其它任意键中止):").strip().lower()
    except EOFError:   # 非交互环境(无 stdin)一律视为未确认
        reply = ""
    if reply not in ("y", "yes"):
        sys.exit("已中止:未覆盖 VPS 数据卷。日常更新请用 deploy/update_dist.py")


def main():
    assume_yes = "--yes" in sys.argv
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
    data_files = _data_files()
    if data_files and not assume_yes:
        _confirm_data_overwrite([os.path.basename(local) for local, _ in data_files])
    for local, remote in data_files:
        dc.upload(ssh, local, remote)
        print(f"    数据文件 {os.path.basename(local)} → {remote}")

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
