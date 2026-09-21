FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.13

# Install system libraries needed by full opencv-python
RUN dnf install -y mesa-libGL glib2 2>/dev/null || true

# Install opencv-python (full, not headless) for broader codec support
RUN pip install --no-cache-dir opencv-python numpy boto3

# Copy black-frame detector files
COPY worker.py merger.py launcher.py ${LAMBDA_TASK_ROOT}/

# The model-based detector builds from Dockerfile.model in this same context
CMD ["worker.handler"]
