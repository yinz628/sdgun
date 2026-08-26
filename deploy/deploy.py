# -*- coding: utf-8 -*-
"""SDGun Docker 部署脚本：本机 → VPS(192.168.2.197)"""
import os
import posixpath
import stat
import sys

import paramiko

HOST, USER, PASSWORD = "192.168.2.197", "root", "nature123"
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # F:\rchang\sdgun
REMOTE_APP = "/opt/sdgun/app"
REMOTE_DATA = "/opt/sdgun/data"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=12)
print(f"[1] 已连接 {HOST}")


def run(cmd, timeout=120, check=True, quiet=False):
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    if not quiet:
        if out:
            print(f"    {out[:600]}")
        if err:
            print(f"    [stderr] {err[:400]}")
    if check and code != 0:
        print(f"    [退出码 {code}] 命令失败: {cmd[:120]}")
        sys.exit(1)
    return out


def upload(local, remote):
    sftp = ssh.open_sftp()
    rd = posixpath.dirname(remote)
    if rd:
        run(f"mkdir -p '{rd}'", quiet=True)
    sftp.put(local, remote)
    sftp.close()


def upload_tree(local_dir, remote_dir):
    sftp = ssh.open_sftp()
    count = 0
    for root, _dirs, files in os.walk(local_dir):
        rel = os.path.relpath(root, local_dir).replace("\\", "/")
        rdir = remote_dir if rel == "." else f"{remote_dir}/{rel}"
        try:
            sftp.stat(rdir)
        except FileNotFoundError:
            run(f"mkdir -p '{rdir}'", quiet=True)
        for f in files:
            sftp.put(os.path.join(root, f), f"{rdir}/{f}")
            count += 1
    sftp.close()
    return count


# ---- 2. 环境检查 ----
print("[2] 环境检查")
run("cat /etc/os-release | head -2", quiet=False)
docker_ok = run("docker --version", check=False, quiet=True)
if docker_ok:
    print(f"    Docker 已安装: {docker_ok}")
else:
    print("    未安装 Docker，开始安装（get.docker.com）...")
    run("curl -fsSL https://get.docker.com | sh", timeout=600)
    run("systemctl enable --now docker", timeout=60)
    print("    Docker 安装完成:", run("docker --version", quiet=True))

# ---- 3. 上传文件 ----
print("[3] 上传文件")
run(f"mkdir -p {REMOTE_APP} {REMOTE_DATA}", quiet=True)
# 代理三模块(sdgun_proxy 依赖 sdgun_session/sdgun_sign,漏传会 import 失败)
for _mod in ("sdgun_proxy.py", "sdgun_session.py", "sdgun_sign.py"):
    upload(os.path.join(LOCAL_ROOT, _mod), f"{REMOTE_APP}/{_mod}")
upload(os.path.join(LOCAL_ROOT, "deploy", "Dockerfile"), f"{REMOTE_APP}/Dockerfile")
upload(os.path.join(LOCAL_ROOT, "deploy", "requirements.txt"), f"{REMOTE_APP}/requirements.txt")
n = upload_tree(os.path.join(LOCAL_ROOT, "webui", "dist"), f"{REMOTE_APP}/webui/dist")
print(f"    前端构建产物 {n} 个文件")
for f in os.listdir(LOCAL_ROOT):
    if f.startswith("sdgun") and f.endswith(".json"):
        upload(os.path.join(LOCAL_ROOT, f), f"{REMOTE_DATA}/{f}")
        print(f"    数据文件 {f}")

# ---- 4. 构建并运行 ----
print("[4] 构建镜像")
run(f"cd {REMOTE_APP} && docker build -t sdgun .", timeout=600)
print("[5] 运行容器")
run("docker rm -f sdgun 2>/dev/null; true", check=False, quiet=True)
run("docker run -d --name sdgun --restart unless-stopped "
    "--sysctl net.ipv6.conf.all.disable_ipv6=1 "
    "-p 50002:8000 "
    f"-v {REMOTE_DATA}:/data sdgun", timeout=60)
run("sleep 4 && docker ps --filter name=sdgun --format '{{.Names}} {{.Status}} {{.Ports}}'")
print("[6] 启动日志")
run("docker logs sdgun 2>&1 | head -8")

# ---- 7. 容器内自检 ----
print("[7] 服务自检（VPS 本机）")
info = run("curl -s --max-time 10 http://127.0.0.1:50002/proxy-info", check=False)
print("    proxy-info OK" if '"proxy"' in info else f"    异常: {info[:200]}")

ssh.close()
print("\n部署完成：http://192.168.2.197:50002")
