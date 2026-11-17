FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -c requirements.lock -r requirements.txt
COPY backend ./backend
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
USER app
ENV DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1"]
