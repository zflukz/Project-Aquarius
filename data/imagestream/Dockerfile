FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir confluent-kafka==2.5.3
COPY imagestream/server.py /app/server.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "/app/server.py"]
