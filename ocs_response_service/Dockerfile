FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app/ocs_response_service

EXPOSE 8000
CMD ["uvicorn", "ocs_response_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
