# aquarius-imagestream — live MJPEG of every drone from Kafka image-frames (Mission dashboard camera, :8090).
# Profile `pipeline` in docker/compose.*.yml.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir confluent-kafka==2.5.3
COPY server.py /app/server.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "/app/server.py"]
