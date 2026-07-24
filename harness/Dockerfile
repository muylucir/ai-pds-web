# harness/Dockerfile — layered over the managed al2023 base (supplied at build
# time via MicrovmImage BaseImageArn; this Dockerfile only adds our app layer).
FROM public.ecr.aws/amazonlinux/amazonlinux:2023
# shadow-utils provides useradd (absent from the minimal al2023 base).
# nodejs/npm: NOT for Claude Code (the agent-sdk wheel bundles its own native
# binary) — they are the runtime the agent uses to build/run the prototype.
RUN dnf install -y python3.11 python3.11-pip nodejs npm shadow-utils git && dnf clean all
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r /app/requirements.txt
COPY *.py /app/
# Prototype-building rules baked into the workspace.
COPY aiplc-rules /workspace/aiplc-rules
# Run as a NON-ROOT user. The SDK spawns the same claude binary, which
# hard-refuses bypassPermissions when euid==0 (learned at 6d21e1f: version
# checks pass as root but every turn exits 1).
RUN useradd -m -u 10001 harness \
    && chown -R harness:harness /workspace /app
USER harness
EXPOSE 8080 9000
CMD ["python3.11", "-m", "serve"]
