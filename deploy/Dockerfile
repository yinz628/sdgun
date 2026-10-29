FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代理七模块(sdgun_proxy 依赖 sdgun_session / sdgun_sign / sdgun_messages / sdgun_likes / sdgun_favorites / sdgun_watch)
COPY sdgun_proxy.py sdgun_session.py sdgun_sign.py sdgun_messages.py sdgun_likes.py sdgun_favorites.py sdgun_watch.py ./
COPY webui/dist ./webui/dist

# 抓包/会话数据目录（运行时挂载宿主机目录）
ENV SDGUN_DATA_DIR=/data

# 健康探针:/proxy-info 不依赖上游;让 docker ps 的 health 状态可观测
# (slim 镜像无 curl,用 python 标准库;2xx 之外 urlopen 抛错即 unhealthy)
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/proxy-info', timeout=5)"]

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "sdgun_proxy:app", "--host", "0.0.0.0", "--port", "8000"]
