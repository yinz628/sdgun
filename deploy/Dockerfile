FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sdgun_proxy.py .
COPY webui/dist ./webui/dist

# 抓包/会话数据目录（运行时挂载宿主机目录）
ENV SDGUN_DATA_DIR=/data

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "sdgun_proxy:app", "--host", "0.0.0.0", "--port", "8000"]
