"""
Visual Boundary Detector — Launcher Lambda

Lists videos in S3, builds chunk definitions, and starts the Step Functions
batch execution for parallel processing.

Input event:
  {
    "s3_bucket": "my-bucket",
    "s3_prefix": "video/",
    "video_names": ["video1", "video2"]  // optional filter
  }
"""

import json
import os
import boto3

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
CHUNK_DURATION_SEC = 60     # Duration of each processing chunk
SCAN_MARGIN_SEC = 300       # Skip first N seconds (pre-content)
VIDEO_DURATION_SEC = 3660   # Assumed max video duration
# ─────────────────────────────────────────────────────────────────────────────

s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


def handler(event, context):
    bucket = event["s3_bucket"]
    prefix = event.get("s3_prefix", "video/")
    filter_names = event.get("video_names")

    # List all video files
    paginator = s3.get_paginator("list_objects_v2")
    videos = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".mp4"):
                name = key.rsplit("/", 1)[-1].replace(".mp4", "")
                if filter_names and name not in filter_names:
                    continue
                videos.append({"name": name, "key": key})

    # Build chunk definitions
    scan_start = SCAN_MARGIN_SEC
    scan_end = VIDEO_DURATION_SEC
    chunks_template = []
    chunk_id = 0
    t = scan_start
    while t < scan_end:
        chunks_template.append({"chunk_id": chunk_id})
        chunk_id += 1
        t += CHUNK_DURATION_SEC

    # Build per-video items
    video_items = []
    for vid in videos:
        chunks = [{"chunk_id": c["chunk_id"]} for c in chunks_template]
        video_items.append({
            "video_name": vid["name"],
            "s3_bucket": bucket,
            "s3_key": vid["key"],
            "chunks": chunks,
        })

    print(f"Launching batch for {len(video_items)} videos, "
          f"{len(chunks_template)} chunks each")

    execution = sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps({
            "s3_bucket": bucket,
            "videos": video_items,
        }),
    )

    return {
        "execution_arn": execution["executionArn"],
        "videos": len(video_items),
        "total_chunks": len(video_items) * len(chunks_template),
    }
