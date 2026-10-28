FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY widget ./widget
COPY chat ./chat
COPY admin ./admin
RUN useradd --system --uid 10001 --create-home chatbot && mkdir -p /srv/uploads && chown -R chatbot:chatbot /srv
USER chatbot
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
