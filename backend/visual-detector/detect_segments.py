"""Ticker-based commercial segment detection.

Detects commercials by comparing each frame's ticker-region histogram
against a precomputed network color profile. Frames dissimilar to the
profile (no ticker visible) are classified as commercial.
"""

import cv2
import numpy as np

# Histogram parameters — must match ColorProfile.py exactly
H_BINS = 36
S_BINS = 32
V_BINS = 32
HIST_SIZE = [H_BINS, S_BINS, V_BINS]
HIST_RANGES = [0, 180, 0, 256, 0, 256]
CHANNELS = [0, 1, 2]
MARGIN_START_SEC = 300
MARGIN_END_SEC = 60


def frame_histogram(frame, crop_top_fraction=0.75, crop_bottom_fraction=1.0,
                    crop_left_fraction=0.0, crop_right_fraction=1.0):
    """Compute L1-normalized HSV histogram of the ticker region."""
    h, w = frame.shape[:2]
    top = int(h * crop_top_fraction)
    bottom = int(h * crop_bottom_fraction)
    left = int(w * crop_left_fraction)
    right = int(w * crop_right_fraction)
    crop = frame[top:bottom, left:right, :]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], CHANNELS, None, HIST_SIZE, HIST_RANGES)
    cv2.normalize(hist, hist, alpha=1.0, beta=0.0, norm_type=cv2.NORM_L1)
    return hist.flatten().astype(np.float64)


def chi_square_distance(h1, h2):
    """Symmetric chi-square distance between two histograms."""
    denom = h1 + h2 + 1e-10
    return 0.5 * float(np.sum((h1 - h2) ** 2 / denom))


def find_segments(video_path, profile, threshold=0.35, scan_fps=1, crop_config=None):
    """Scan video and find dissimilar (commercial) segments."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps / scan_fps))

    crop_kwargs = {}
    if crop_config:
        crop_kwargs = {
            'crop_top_fraction': crop_config.get('crop_top_fraction', 0.75),
            'crop_bottom_fraction': crop_config.get('crop_bottom_fraction', 1.0),
            'crop_left_fraction': crop_config.get('crop_left_fraction', 0.0),
            'crop_right_fraction': crop_config.get('crop_right_fraction', 1.0),
        }

    dissimilar_timestamps = []

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
                hist = frame_histogram(frame, **crop_kwargs)
                dist = chi_square_distance(hist, profile)

                if dist > threshold:
                    dissimilar_timestamps.append(timestamp)

        frame_num += 1

    video_duration = total_frames / fps
    cap.release()

    if not dissimilar_timestamps:
        return [], video_duration

    # Group consecutive timestamps into segments
    max_gap = (step / fps) * 2
    segments = []
    seg_start = dissimilar_timestamps[0]
    seg_end = dissimilar_timestamps[0]

    for ts in dissimilar_timestamps[1:]:
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
        if duration < 10:
            if i + 1 < len(segments):
                gap = segments[i + 1]["start_time"] - seg["end_time"]
                if gap > 5:
                    continue
            else:
                continue
        filtered.append(seg)

    # Step 2: merge segments less than 10 seconds apart
    merged = []
    for seg in filtered:
        if merged and seg["start_time"] - merged[-1]["end_time"] < 10:
            merged[-1]["end_time"] = seg["end_time"]
            merged[-1]["duration"] = round(merged[-1]["end_time"] - merged[-1]["start_time"], 3)
        else:
            merged.append(dict(seg))

    # Step 3: remove segments shorter than 20 seconds
    result = [seg for seg in merged if seg["end_time"] - seg["start_time"] >= 20]

    # Step 4: remove segments within opening/closing margins
    cutoff_end = video_duration - MARGIN_END_SEC
    result = [seg for seg in result if seg["start_time"] >= MARGIN_START_SEC and seg["start_time"] <= cutoff_end]

    return result
