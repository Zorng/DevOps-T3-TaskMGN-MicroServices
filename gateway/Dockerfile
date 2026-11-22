# Multi-arch official image (amd64 and arm64), so it runs on Oracle ARM VMs.
FROM nginx:1.28-alpine

# The official image renders every *.template in this folder with envsubst
# at start-up, so upstream addresses stay outside the image.
COPY nginx/templates/ /etc/nginx/templates/

# Defaults make the image start standalone; deployments override them.
ENV IDENTITY_UPSTREAM=http://identity-workspace:8000 \
    TASK_UPSTREAM=http://task:8001 \
    NOTIFICATION_UPSTREAM=http://notification:8002

# Browser origins allowed by CORS: a regex alternation of exact origins (use
# [.] for dots). Defaults are the local frontend dev origins; set the exact
# production frontend origin at deploy time. No wildcards.
ENV CORS_ALLOWED_ORIGINS=http://localhost:5173|http://127[.]0[.]0[.]1:5173

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1