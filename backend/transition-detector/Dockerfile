FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.13

# Install system libraries needed by opencv-python
RUN dnf install -y mesa-libGL glib2 2>/dev/null || true

# Install dependencies
RUN pip install --no-cache-dir opencv-python numpy boto3

# Copy handler and detection code
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]
