"""
Visual Boundary Detector — Worker Lambda

Scans a chunk of video for visual boundaries (black frames, scene changes).
Designed to run in parallel across many chunks of a single video.

Input event:
  {
    "s3_bucket": "my-bucket",
    "s3_key": "video/my-video.mp4",
    "start_sec": 300,
    "end_sec": 360,
    "chunk_id": 0
  }

Output:
  {
    "chunk_id": 0,
    "start_sec": 300,
    "end_sec": 360,
    "boundary_runs": [{"start": 305.2, "end": 305.8}, ...]
  }

Customize: Modify detection thresholds or add additional detection methods
(scene change detection, logo detection, etc.)
"""

import os
import numpy as np
import cv2


# ─── CONFIGURATION (customize these for your use case) ───────────────────────
BOUNDARY_THRESHOLD = 0.3       # Histogram distance threshold for boundary detection
SUSPICIOUS_THRESHOLD = 8.0     # Secondary threshold for potential boundaries
MIN_BOUNDARY_DURATION = 0.1    # Minimum boundary run duration (seconds)
BOUNDARY_GAP_SEC = 15          # Minimum gap between distinct boundary events
# ─────────────────────────────────────────────────────────────────────────────


def compute_histogram(region, bins=32):
    """Compute color histogram features for a frame region."""
    hist_features = []
    for i in range(3):
        hist = cv2.calcHist([region], [i], None, [bins], [0, 256])
        hist = cv2.normalize(hist, hist).flatten()
        hist_features.extend(hist)
    return np.array(hist_features)


def histogram_distance(h1, h2):
    """Chi-squared distance between two histograms."""
    eps = 1e-10
    return np.sum((h1 - h2) ** 2 / (h1 + h2 + eps))


# Reference histogram for a pure black frame
_BLACK_HIST = np.zeros(32 * 3)
_BLACK_HIST[0] = 1.0
_BLACK_HIST[32] = 1.0
_BLACK_HIST[64] = 1.0


def is_boundary_frame(frame, threshold=BOUNDARY_THRESHOLD):
    """Check if a frame matches the boundary pattern (default: black frame)."""
    return histogram_distance(compute_histogram(frame), _BLACK_HIST) < threshold


def download_chunk(bucket, key, start_sec, end_sec, local_path):
    """Generate a presigned URL for OpenCV to read directly."""
    import boto3
    s3 = boto3.client("s3")
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=900
    )


def analyze_chunk(video_path, start_sec, end_sec):
    """Scan a video chunk for visual boundaries.

    For short chunks (≤15s): frame-by-frame scan.
    For normal chunks: coarse 1fps scan with refinement.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        cap.release()
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    chunk_duration = end_sec - start_sec

    # Detect if reading from full video or extracted chunk
    file_duration = total_frames / fps
    is_full_video = file_duration > chunk_duration * 1.5

    if is_full_video:
        start_frame = int(start_sec * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    else:
        start_frame = 0

    chunk_frames = int(chunk_duration * fps)

    # Short chunks: frame-by-frame scan
    if chunk_duration <= 15:
        boundary_runs = []
        in_boundary = False
        run_start = 0
        for i in range(chunk_frames):
            ret, frame = cap.read()
            if not ret:
                break
            current_time = start_sec + i / fps
            if is_boundary_frame(frame):
                if not in_boundary:
                    run_start = current_time
                    in_boundary = True
            else:
                if in_boundary:
                    boundary_runs.append({"start": round(run_start, 3),
                                          "end": round(current_time, 3)})
                    in_boundary = False
        if in_boundary:
            boundary_runs.append({"start": round(run_start, 3),
                                  "end": round(start_sec + chunk_frames / fps, 3)})
        cap.release()
        return boundary_runs

    # Normal chunks: coarse 1fps scan with refinement
    skip = max(1, int(fps))
    coarse_hits = []
    last_hit = -10000

    for i in range(0, chunk_frames, skip):
        frame_num = start_frame + i
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            break

        dist = histogram_distance(compute_histogram(frame), _BLACK_HIST)
        if dist < BOUNDARY_THRESHOLD:
            if (i - last_hit) >= fps * 5:
                coarse_hits.append(frame_num)
            last_hit = i
        elif dist < SUSPICIOUS_THRESHOLD:
            if not any(abs(frame_num - c) < fps * 3 for c in coarse_hits):
                coarse_hits.append(frame_num)

    coarse_hits.sort()

    # Refine each coarse detection to frame-accurate boundaries
    boundary_runs = []
    window = int(2 * fps)
    for approx_frame in coarse_hits:
        search_start = max(start_frame, approx_frame - window)
        search_end = min(start_frame + chunk_frames, approx_frame + window)
        cap.set(cv2.CAP_PROP_POS_FRAMES, search_start)
        run_start = None
        for f in range(search_start, search_end):
            ret, frame = cap.read()
            if not ret:
                break
            t = start_sec + (f - start_frame) / fps
            if is_boundary_frame(frame):
                if run_start is None:
                    run_start = t
            else:
                if run_start is not None:
                    boundary_runs.append({"start": round(run_start, 3),
                                          "end": round(t, 3)})
                    run_start = None
                    break
        if run_start is not None:
            boundary_runs.append({"start": round(run_start, 3),
                                  "end": round(start_sec + (search_end - start_frame) / fps, 3)})

    cap.release()
    return boundary_runs


def handler(event, context):
    """Lambda entry point."""
    bucket = event["s3_bucket"]
    key = event["s3_key"]
    chunk_id = event["chunk_id"]

    margin = int(os.environ.get("SCAN_MARGIN_SEC", 300))
    chunk_dur = int(os.environ.get("CHUNK_DURATION_SEC", 60))
    start_sec = event.get("start_sec", margin + chunk_id * chunk_dur)
    end_sec = event.get("end_sec", start_sec + chunk_dur)

    url = download_chunk(bucket, key, start_sec, end_sec, None)
    boundary_runs = analyze_chunk(url, start_sec, end_sec)

    return {
        "chunk_id": chunk_id,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "boundary_runs": boundary_runs,
    }
