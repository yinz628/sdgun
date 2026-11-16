FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# pip 默认走阿里云镜像(国内 VPS 拉 PyPI 经常慢到拖死 docker build;
# 清华源实测对部分网络段返回 403,2026-11-16 弃用);
# ARG 可覆盖,海外构建时 --build-arg PIP_INDEX_URL=https://pypi.org/simple 即可
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

# 代理十模块(sdgun_proxy 依赖 sdgun_session / sdgun_store / sdgun_sign / sdgun_messages / sdgun_likes / sdgun_favorites / sdgun_watch / sdgun_uistate / sdgun_parse)
COPY sdgun_proxy.py sdgun_session.py sdgun_store.py sdgun_sign.py sdgun_messages.py sdgun_likes.py sdgun_favorites.py sdgun_watch.py sdgun_uistate.py sdgun_parse.py ./
COPY webui/dist ./webui/dist

# 抓包/会话数据目录（运行时挂载宿主机目录）
ENV SDGUN_DATA_DIR=/data

# 健康探针:/proxy-info 不依赖上游;让 docker ps 的 health 状态可观测
# (slim 镜像无 curl,用 python 标准库;2xx 之外 urlopen 抛错即 unhealthy)
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/proxy-info', timeout=5)"]

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "sdgun_proxy:app", "--host", "0.0.0.0", "--port", "8000"]
