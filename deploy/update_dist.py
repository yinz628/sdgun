# -*- coding: utf-8 -*-
"""SDGun 前端增量部署：本机 → VPS(192.168.2.197)，只更新 webui/dist 并重建容器。

与 deploy.py(全量)的区别：**绝不上传本地 sdgun*.json**。本地抓包/session 文件可能
过期(如本机 sdgun_session.json 已失效)，全量上传会把 VPS 上仍有效的会话文件盖掉，
导致整站"登录信息已过期"。后端 .py 未变时用本脚本，秒级完成、会话零风险。

用法：python deploy/update_dist.py
"""
import os
import posixpath

import paramiko

HOST, USER, PASSWORD = "192.168.2.197", "root", "nature123"
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # F:\rchang\sdgun
LOCAL_DIST = os.path.join(LOCAL_ROOT, "webui", "dist")
REMOTE_APP = "/opt/sdgun/app"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=12)
print(f"[1] 已连接 {HOST}")


def run(cmd, timeout=300, check=True):
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    if out:
        print(f"    {out[:400]}")
    if err:
        print(f"    [stderr] {err[:300]}")
    if check and code != 0:
        raise SystemExit(f"[退出码 {code}] 命令失败: {cmd[:120]}")
    return out


print("[2] 上传 webui/dist（旧容器继续服务，不受影响）")
run(f"rm -rf {REMOTE_APP}/webui/dist && mkdir -p {REMOTE_APP}/webui/dist")
sftp = ssh.open_sftp()
count = 0
for root, _dirs, files in os.walk(LOCAL_DIST):
    rel = os.path.relpath(root, LOCAL_DIST).replace("\\", "/")
    rdir = f"{REMOTE_APP}/webui/dist" if rel == "." else f"{REMOTE_APP}/webui/dist/{rel}"
    try:
        sftp.stat(rdir)
    except FileNotFoundError:
        run(f"mkdir -p '{rdir}'")
    for f in files:
        sftp.put(os.path.join(root, f), posixpath.join(rdir, f))
        count += 1
sftp.close()
print(f"    已上传 {count} 个文件")

print("[3] 构建镜像")
run(f"cd {REMOTE_APP} && docker build -t sdgun .", timeout=600)

print("[4] 换容器（与 deploy.py 完全相同的参数）")
run("docker rm -f sdgun 2>/dev/null; true", check=False)
run("docker run -d --name sdgun --restart unless-stopped "
    "--sysctl net.ipv6.conf.all.disable_ipv6=1 "
    "-p 50002:8000 "
    "-v /opt/sdgun/data:/data sdgun", timeout=60)
run("sleep 4 && docker ps --filter name=sdgun --format '{{.Names}} {{.Status}} {{.Ports}}'")

print("[5] 自检")
run("sleep 3 && curl -s --max-time 10 http://127.0.0.1:50002/health")
run("curl -s --max-time 10 http://127.0.0.1:50002/ | grep -o 'assets/index-[^\"]*\\.js' | head -1")

ssh.close()
print("\n前端增量部署完成：http://192.168.2.197:50002")
