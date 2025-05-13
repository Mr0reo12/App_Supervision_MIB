# ────────────────────────────── backend ──
# Use a lightweight Python base image
FROM python:3.13-alpine

# Python settings: no .pyc files, unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user (UID/GID 1001)
RUN addgroup -g 1001 app && adduser -D -G app -u 1001 app

# Set working directory
WORKDIR /app

# Copy only requirements first for better layer caching
COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source code
COPY . .

# Change ownership to the non-root user
RUN chown -R app:app /app
USER app

EXPOSE 5001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5001"]
