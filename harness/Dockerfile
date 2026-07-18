# harness/Dockerfile — layered over the managed al2023 base (supplied at build
# time via MicrovmImage BaseImageArn; this Dockerfile only adds our app layer).
FROM public.ecr.aws/amazonlinux/amazonlinux:2023
RUN dnf install -y python3.11 python3.11-pip nodejs npm && dnf clean all
RUN npm install -g @anthropic-ai/claude-code
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r /app/requirements.txt
COPY *.py /app/
# Rules baked into the workspace the harness serves from.
COPY aiplc-rules /workspace/aiplc-rules
EXPOSE 8080 9000
CMD ["python3.11", "-m", "serve"]
