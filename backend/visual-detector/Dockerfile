FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.13

# Install system libraries needed by full opencv-python
RUN dnf install -y mesa-libGL glib2 2>/dev/null || true

# Install opencv-python (full, not headless) for broader codec support
RUN pip install --no-cache-dir opencv-python numpy boto3

# Copy black-frame detector files
COPY worker.py merger.py launcher.py ${LAMBDA_TASK_ROOT}/

# Copy ticker detector files
COPY visual_label_detector.py detect_segments.py color_profile_builder.py ${LAMBDA_TASK_ROOT}/

# Default CMD is black-frame worker; label detector uses a separate Lambda definition
CMD ["worker.handler"]
