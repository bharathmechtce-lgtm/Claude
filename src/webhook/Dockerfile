FROM python:3.11-slim

WORKDIR /app

COPY webhook/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY .env ./.env
COPY webhook/ ./webhook/

EXPOSE 8000

CMD ["uvicorn", "webhook.app:app", "--host", "0.0.0.0", "--port", "8000"]
