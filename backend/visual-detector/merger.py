"""
Visual Boundary Detector — Merger Lambda

Collects chunk results for a video, pairs boundary events into segments,
and writes final results to S3.

Customize: Modify pairing logic for your boundary type
(e.g., scene changes don't need pairing, just mark transitions).

Input (from Step Functions):
  {
    "video_name": "my-video",
    "s3_bucket": "my-bucket",
    "s3_key": "video/my-video.mp4",
    "chunk_results": [ ... array of worker outputs ... ]
  }
"""

import json
import os
import boto3

# ─── CONFIGURATION (customize for your use case) ─────────────────────────────
SCAN_MARGIN_START_SEC = 300   # Skip first N seconds
SCAN_MARGIN_END_SEC = 60      # Skip last N seconds
BOUNDARY_GAP_SEC = 15         # Min gap between distinct events
MIN_BOUNDARY_DURATION = 0.1   # Min run duration to count
MIN_SEGMENT_SEC = 90          # Min segment duration
MAX_SEGMENT_SEC = 300         # Max segment duration
STANDARD_DURATIONS = list(range(30, MAX_SEGMENT_SEC + 1, 30))  # Expected durations
# ─────────────────────────────────────────────────────────────────────────────

s3 = boto3.client("s3")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "visual_results/")


def merge_boundary_runs(all_results):
    """Merge adjacent boundary runs from multiple chunks."""
    runs = []
    for result in sorted(all_results, key=lambda r: r.get("start_sec", 0)):
        runs.extend(result.get("boundary_runs", []))
    merged = []
    for run in runs:
        if merged and run["start"] - merged[-1]["end"] < 0.1:
            merged[-1]["end"] = run["end"]
        else:
            merged.append(dict(run))
    return [r for r in merged if r["end"] - r["start"] >= MIN_BOUNDARY_DURATION]


def extract_boundary_events(runs):
    """Extract discrete boundary events from continuous runs."""
    events = []
    last_end = -10000
    for run in runs:
        if run["start"] - last_end >= BOUNDARY_GAP_SEC:
            events.append(run["start"])
        last_end = run["end"]
    return events


def pair_boundaries(events):
    """Pair boundary events into segments using duration heuristics."""
    events = sorted(set(events))
    segments, used = [], set()

    for i in range(len(events)):
        if i in used:
            continue
        best_j, best_score, best_dur = -1, float("inf"), 0
        for j in range(i + 1, len(events)):
            if j in used:
                continue
            dur = events[j] - events[i]
            if dur < MIN_SEGMENT_SEC:
                continue
            if dur > MAX_SEGMENT_SEC:
                break
            score = min(abs(dur - sd) for sd in STANDARD_DURATIONS)
            if best_score < 3 and score > best_score:
                break
            if score < best_score or (score == best_score and dur > best_dur):
                best_score = score
                best_j = j
                best_dur = dur
        if best_j >= 0:
            segments.append({
                "start_time": events[i],
                "end_time": events[best_j],
                "duration": round(events[best_j] - events[i], 2),
            })
            used.add(i)
            used.add(best_j)

    return segments


def handler(event, context):
    video_name = event["video_name"]
    bucket = event["s3_bucket"]
    chunk_results = event.get("chunk_results", [])

    print(f"[MERGER] video={video_name}, {len(chunk_results)} chunk results")

    # Get video duration from chunks
    duration = max((r.get("end_sec", 0) for r in chunk_results), default=3600)
    scan_start = SCAN_MARGIN_START_SEC
    scan_end = duration - SCAN_MARGIN_END_SEC

    # Process boundaries
    runs = merge_boundary_runs(chunk_results)
    events = extract_boundary_events(runs)
    events = [t for t in events if scan_start <= t <= scan_end]

    # Pair into segments
    segments = pair_boundaries(events)
    segments.sort(key=lambda s: s["start_time"])

    # Write output
    output = {
        "video": video_name,
        "segments": segments,
        "boundary_events": [round(t, 3) for t in events],
    }

    result_key = f"{OUTPUT_PREFIX}{video_name}_segments.json"
    s3.put_object(
        Bucket=bucket,
        Key=result_key,
        Body=json.dumps(output, indent=2),
        ContentType="application/json",
    )

    print(f"[MERGER] {len(segments)} segments → s3://{bucket}/{result_key}")

    return {
        "video": video_name,
        "segments_count": len(segments),
        "result_key": result_key,
    }
