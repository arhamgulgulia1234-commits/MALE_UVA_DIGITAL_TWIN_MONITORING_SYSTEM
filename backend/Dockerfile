FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Render (and most PaaS) inject the port to bind on via $PORT. Binding to a hardcoded
# port means the platform's health check never reaches the app and the deploy is marked
# failed. Shell form so the variable is actually expanded.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
