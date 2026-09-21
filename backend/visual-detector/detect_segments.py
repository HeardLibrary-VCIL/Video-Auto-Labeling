"""Model-based commercial segment detection.

Detects commercials by classifying each sampled frame with a trained
pixel classifier. The model resizes the frame, reads the pixel
positions it was trained on, scales those values, and predicts a
commercial probability. Frames whose probability exceeds the threshold are treated
as commercial and grouped into segments.
"""

import cv2
import numpy as np

# Post-processing parameters
MARGIN_START_SEC = 300     # ignore detections before this many seconds
MARGIN_END_SEC = 60        # ignore detections after (duration - this)
MIN_SEGMENT_SEC = 20       # drop segments shorter than this after merge
MERGE_GAP_SEC = 10         # merge segments whose gap is smaller than this
STRAY_DURATION_SEC = 10    # lone short segments (< this) preceded by large gap are dropped
STRAY_GAP_SEC = 5          # gap threshold for "lone" check above


def frame_features(frame, model):
    """Extract the pixel values the classifier was trained on."""
    small = cv2.resize(frame, (model["frame_w"], model["frame_h"]))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    flat = hsv.flatten().astype(np.float32)
    return flat[model["pixel_indices"]].reshape(1, -1)


def find_segments(video_path, model, threshold=0.5, scan_fps=1):
    """Scan video and find commercial segments using the trained classifier.

    The classifier operates on the full frame exactly as it was trained.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps / scan_fps))

    scaler = model["scaler"]
    classifier = model["classifier"]

    commercial_timestamps = []

    # Use grab()/retrieve() pattern: grab() advances without decoding,
    # retrieve() decodes only frames we need. Much faster for large files.
    frame_num = 0
    while True:
        grabbed = cap.grab()
        if not grabbed:
            break

        if frame_num % step == 0:
            ret, frame = cap.retrieve()
            if ret:
                timestamp = frame_num / fps
                feat = frame_features(frame, model)
                feat_sc = scaler.transform(feat)
                prob = classifier.predict_proba(feat_sc)[0, 1]

                if prob > threshold:
                    commercial_timestamps.append(timestamp)

        frame_num += 1

    video_duration = total_frames / fps
    cap.release()

    if not commercial_timestamps:
        return [], video_duration

    # Group consecutive timestamps into segments
    max_gap = (step / fps) * 2
    segments = []
    seg_start = commercial_timestamps[0]
    seg_end = commercial_timestamps[0]

    for ts in commercial_timestamps[1:]:
        if ts - seg_end <= max_gap:
            seg_end = ts
        else:
            segments.append((seg_start, seg_end))
            seg_start = ts
            seg_end = ts
    segments.append((seg_start, seg_end))

    return [
        {
            "start_time": round(start, 3),
            "end_time": round(end, 3),
            "duration": round(end - start, 3),
        }
        for start, end in segments
    ], video_duration


def cleanup(segments, video_duration):
    """Filter, merge, and clean detected segments."""
    # Step 1: remove short segments not immediately followed by another
    filtered = []
    for i, seg in enumerate(segments):
        duration = seg["end_time"] - seg["start_time"]
        if duration < STRAY_DURATION_SEC:
            if i + 1 < len(segments):
                gap = segments[i + 1]["start_time"] - seg["end_time"]
                if gap > STRAY_GAP_SEC:
                    continue
            else:
                continue
        filtered.append(seg)

    # Step 2: merge segments less than MERGE_GAP_SEC apart
    merged = []
    for seg in filtered:
        if merged and seg["start_time"] - merged[-1]["end_time"] < MERGE_GAP_SEC:
            merged[-1]["end_time"] = seg["end_time"]
            merged[-1]["duration"] = round(merged[-1]["end_time"] - merged[-1]["start_time"], 3)
        else:
            merged.append(dict(seg))

    # Step 3: remove segments shorter than MIN_SEGMENT_SEC
    result = [seg for seg in merged if seg["end_time"] - seg["start_time"] >= MIN_SEGMENT_SEC]

    # Step 4: remove segments within opening/closing margins
    cutoff_end = video_duration - MARGIN_END_SEC
    result = [seg for seg in result if seg["start_time"] >= MARGIN_START_SEC and seg["start_time"] <= cutoff_end]

    return result
