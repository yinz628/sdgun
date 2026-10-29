# -*- coding: utf-8 -*-
"""部署公共层:SSH 连接、远程命令、上传、容器运行参数(单一事实来源)。

deploy.py(全量)/update_dist.py(增量)/monitor.py(监控)共用。此前三个脚本
各自维护一份连接凭据与 docker run 参数——2026-09-10 的 DNS 事故修复就要求
"两个部署脚本都要加 --dns",重复即同步负担;现在容器参数只此一处。

凭据一律从环境变量读取,不再硬编码进脚本(避免随仓库/AI 工具/备份外流):
  SDGUN_DEPLOY_HOST       目标主机(默认 192.168.2.197)
  SDGUN_DEPLOY_USER       登录用户(默认 root)
  SDGUN_DEPLOY_PASSWORD   密码登录
  SDGUN_DEPLOY_KEYFILE    私钥文件路径(设置了则优先于密码)

用法示例:
  PowerShell:  $env:SDGUN_DEPLOY_PASSWORD='...'; python deploy/update_dist.py
  Git Bash:    SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py
"""
import os
import posixpath
import shlex
import sys

import paramiko

HOST = os.environ.get("SDGUN_DEPLOY_HOST", "192.168.2.197")
USER = os.environ.get("SDGUN_DEPLOY_USER", "root")
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # F:\rchang\sdgun
REMOTE_APP = "/opt/sdgun/app"
REMOTE_DATA = "/opt/sdgun/data"

# 访问令牌门禁 / CORS 白名单:本机设置了对应环境变量才以 -e 注入容器,未设置
# 时不产生任何参数(命令行与历史版本完全一致)。注意 SSH 远端不继承本地环境,
# `-e NAME` 的取值会落到远端 shell 里,因此必须显式下发 NAME=VALUE 并 shell 转义。
_PASSTHROUGH_ENV = ("SDGUN_ACCESS_TOKEN", "SDGUN_CORS_ORIGINS")
_ENV_ARGS = "".join(
    f"-e {name}={shlex.quote(os.environ[name])} "
    for name in _PASSTHROUGH_ENV
    if os.environ.get(name)
)

# 容器运行参数(全部部署脚本共用一份;改这里即全部生效)
DOCKER_RUN = (
    "docker run -d --name sdgun --restart unless-stopped "
    "--sysctl net.ipv6.conf.all.disable_ipv6=1 "
    # 固定公共 DNS(2026-09-10 DNS 事故):宿主 resolv.conf 会被 DHCP 重写,Docker
    # 同步时曾把 IPv4 条目滤掉、只留容器不可达的 IPv6 DNS,导致上游解析全挂;
    # 阿里/DNSPod 公共 DNS 走直连路由可达,与宿主解析彻底解耦
    "--dns 223.5.5.5 --dns 119.29.29.29 "
    # 关注关键字监控固定用版块源「玩具交易区」(fid=176):频道信息流是策展视图会漏
    # 新帖(2026-10-22 实测 7/11 分钟前的新帖不在 cat3 p=1 的 50 条里,而版块
    # threadList 前 50 条就包含),用户关键字全是交易场景,故直接用版块源
    "-e SDGUN_WATCH_FID=176 "
    f"{_ENV_ARGS}"
    "-p 50002:8000 "
    f"-v {REMOTE_DATA}:/data sdgun"
)

# 代理九模块(sdgun_proxy 依赖 sdgun_session/sdgun_store/sdgun_sign/sdgun_messages/sdgun_likes/sdgun_favorites/sdgun_watch/sdgun_uistate,漏传会 import 失败)
PY_MODULES = ("sdgun_proxy.py", "sdgun_session.py", "sdgun_store.py", "sdgun_sign.py",
              "sdgun_messages.py", "sdgun_likes.py", "sdgun_favorites.py",
              "sdgun_watch.py", "sdgun_uistate.py")
DEPLOY_FILES = ("Dockerfile", "requirements.txt")


def connect() -> paramiko.SSHClient:
    """按环境变量里的凭据建 SSH 连接;缺凭据直接退出并提示设置方式。"""
    password = os.environ.get("SDGUN_DEPLOY_PASSWORD")
    keyfile = os.environ.get("SDGUN_DEPLOY_KEYFILE")
    if not password and not keyfile:
        sys.exit("[deploy] 缺少凭据:请设置 SDGUN_DEPLOY_PASSWORD 或 SDGUN_DEPLOY_KEYFILE\n"
                 "       PowerShell: $env:SDGUN_DEPLOY_PASSWORD='...'\n"
                 "       Git Bash:   SDGUN_DEPLOY_PASSWORD=... python deploy/deploy.py")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"hostname": HOST, "username": USER, "timeout": 12}
    if keyfile:
        kwargs["key_filename"] = keyfile
    else:
        kwargs["password"] = password
    ssh.connect(**kwargs)
    print(f"[1] 已连接 {USER}@{HOST}")
    return ssh


def run(ssh, cmd, timeout=120, check=True, quiet=False) -> str:
    """执行远程命令;check=True 时非零退出码直接终止本地脚本。"""
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
        sys.exit(f"    [退出码 {code}] 命令失败: {cmd[:120]}")
    return out


def upload(ssh, local, remote) -> None:
    """上传单个文件(自动建父目录)。"""
    sftp = ssh.open_sftp()
    rd = posixpath.dirname(remote)
    if rd:
        run(ssh, f"mkdir -p '{rd}'", quiet=True)
    sftp.put(local, remote)
    sftp.close()


def upload_tree(ssh, local_dir, remote_dir) -> int:
    """递归上传目录,返回文件数。"""
    sftp = ssh.open_sftp()
    count = 0
    for root, _dirs, files in os.walk(local_dir):
        rel = os.path.relpath(root, local_dir).replace("\\", "/")
        rdir = remote_dir if rel == "." else f"{remote_dir}/{rel}"
        try:
            sftp.stat(rdir)
        except FileNotFoundError:
            run(ssh, f"mkdir -p '{rdir}'", quiet=True)
        for f in files:
            sftp.put(os.path.join(root, f), posixpath.join(rdir, f))
            count += 1
    sftp.close()
    return count


def upload_app_code(ssh) -> None:
    """上传后端四模块 + Dockerfile + requirements(不碰数据卷)。"""
    sftp = ssh.open_sftp()
    for mod in PY_MODULES:
        sftp.put(os.path.join(LOCAL_ROOT, mod), f"{REMOTE_APP}/{mod}")
        print(f"    {mod}")
    for f in DEPLOY_FILES:
        sftp.put(os.path.join(LOCAL_ROOT, "deploy", f), f"{REMOTE_APP}/{f}")
        print(f"    deploy/{f}")
    sftp.close()


def build_image(ssh) -> None:
    run(ssh, f"cd {REMOTE_APP} && docker build -t sdgun .", timeout=600)


def restart_container(ssh) -> None:
    """销毁旧容器并按 DOCKER_RUN 起新容器(数据卷挂载在内,不受影响)。"""
    run(ssh, "docker rm -f sdgun 2>/dev/null; true", check=False, quiet=True)
    run(ssh, DOCKER_RUN, timeout=60)
    run(ssh, "sleep 4 && docker ps --filter name=sdgun --format '{{.Names}} {{.Status}} {{.Ports}}'")
