# harness/Dockerfile — layered over the managed al2023 base (supplied at build
# time via MicrovmImage BaseImageArn; this Dockerfile only adds our app layer).
FROM public.ecr.aws/amazonlinux/amazonlinux:2023
# shadow-utils provides useradd (absent from the minimal al2023 base).
RUN dnf install -y python3.11 python3.11-pip shadow-utils && dnf clean all
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r /app/requirements.txt
COPY *.py /app/
# Rules baked into the workspace the harness serves from.
COPY aiplc-rules /workspace/aiplc-rules
# Run as a NON-ROOT user (least-privilege; the harness writes under /workspace
# and /app, so give the user those).
RUN useradd -m -u 10001 harness \
    && chown -R harness:harness /workspace /app
USER harness
EXPOSE 8080 9000
CMD ["python3.11", "-m", "serve"]
