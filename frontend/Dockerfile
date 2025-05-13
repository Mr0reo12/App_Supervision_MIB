# ───────────────────────────── frontend ──
FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -g 1001 app && adduser -D -G app -u 1001 app
WORKDIR /app

COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R app:app /app
USER app

EXPOSE 5002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5002"]
