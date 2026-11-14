# Experimental, isolated Antigravity ACP runner. The official CLI archive is
# pre-fetched, hash-verified and supplied as a named build context.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin agy
RUN mkdir -p /home/agy/.gemini/antigravity-cli && chown -R 10001:10001 /home/agy/.gemini
COPY --chown=10001:10001 deployment/antigravity/settings.json /home/agy/.gemini/antigravity-cli/settings.json
WORKDIR /opt/agy
COPY --from=acp-runtime /cli_linux_x64.tar.gz /tmp/agy-cli.tar.gz
COPY deployment/antigravity/fetch_runtime.py /tmp/fetch_runtime.py
RUN python /tmp/fetch_runtime.py \
 && rm -f /tmp/fetch_runtime.py

ENV HOME=/home/agy \
    XDG_CONFIG_HOME=/home/agy/.config \
    XDG_CACHE_HOME=/home/agy/.cache \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["/opt/agy/agy", "acp", "--disable-telemetry", "--include-default-customizations=false"]
