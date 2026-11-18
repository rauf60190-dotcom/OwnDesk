FROM caddy:2.10.2 AS caddy
FROM cloudflare/cloudflared:2026.10.7 AS cloudflared
FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OWNDESK_DATA_DIR=/data
WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --create-home --uid 10001 desk
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
# Both services listen on unprivileged ports. File capabilities must not request
# privileges excluded by the runtime's cap_drop: ALL bounding set.
RUN python -c "import os; p='/usr/local/bin/caddy'; os.removexattr(p, 'security.capability') if 'security.capability' in os.listxattr(p) else None"
COPY --chown=desk:desk backend/ /app/backend/
COPY --chown=desk:desk frontend/dist/ /app/frontend/dist/
COPY --chown=desk:desk infra/ /app/infra/
COPY --chown=desk:desk VERSION /app/VERSION
USER desk
CMD ["python", "infra/native_runtime.py", "run", "--data", "/data"]
