FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home mcp
USER 10001
EXPOSE 8080
ENTRYPOINT ["jev-decide-mcp"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8080"]
