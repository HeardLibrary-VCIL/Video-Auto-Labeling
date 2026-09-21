#!/usr/bin/env python3
"""
Train a pixel-based commercial/non-commercial frame classifier from GT data.

Steps:
  1. Load all labeled segments (commercial + non-commercial) from the GT CSV.
  2. Extract frames at ~1 FPS, resize to a small fixed resolution, convert to HSV.
  3. Compute a per-pixel Fisher separation score:
       (mu_commercial - mu_non_commercial)^2 / (var_commercial + var_non_commercial)
     This finds the spatial pixel positions whose HSV values differ most reliably
     between commercial and non-commercial content.
  4. Keep the top-K pixel positions that separate the two classes best.
  5. Train a logistic regression on values at only those positions.
  6. Save the model (.pkl) for use with detect_segments.py --model.

Usage:
  python3 train_model.py --csv fnc_ground_truth.csv --videos FNC_videos --network FNC
  python3 train_model.py --csv GroundTruthData.csv --videos CNN_videos --network CNN --time-unit frames
"""

import argparse
import csv
import os
import pickle
import sys

import cv2
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

DEFAULT_FRAME_W = 64
DEFAULT_FRAME_H = 36
DEFAULT_TOP_K   = 500
DEFAULT_OUTPUT  = "commercial_model.pkl"
SAMPLE_FPS      = 1     # frames sampled per second of segment


# ---------------------------------------------------------------------------
# CSV helpers (same dual-format support as visualizer/ColorProfile)
# ---------------------------------------------------------------------------

def _csv_columns(fieldnames):
    """Detect standard vs alternate (FNC-style) column names."""
    headers = set(fieldnames or [])
    if "file" in headers:
        return "file", "segment_type", "segment_start", "segment_end"
    return "Filename", "SegmentType", "BeginTime", "EndTime"


def load_segments(csv_path, network):
    """Load all labeled segments (both commercial and non-commercial)."""
    segments = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        col_name, col_type, col_begin, col_end = _csv_columns(reader.fieldnames)
        for row in reader:
            seg_type = row.get(col_type, "").strip()
            if not seg_type:
                continue
            try:
                raw_name = row[col_name].strip()
                segments.append({
                    "filename":     raw_name[:8] + network,
                    "segment_type": seg_type,
                    "begin":        float(row[col_begin]),
                    "end":          float(row[col_end]),
                })
            except (KeyError, ValueError):
                continue
    return segments


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def extract_frames(segments, video_dir, time_unit, frame_w, frame_h):
    """
    Sample frames from each GT segment, resize to (frame_w, frame_h), convert
    to HSV, and flatten.

    Returns
    -------
    X : ndarray (n_frames, frame_w * frame_h * 3)  float32
    y : ndarray (n_frames,)  int32  — 1 = commercial, 0 = non-commercial
    """
    X_rows, y_rows = [], []
    open_videos = {}

    try:
        for seg in segments:
            vpath = os.path.join(video_dir, seg["filename"] + ".mp4")
            key   = seg["filename"]

            if key not in open_videos:
                print(f"  Reading: {vpath}")
                cap = cv2.VideoCapture(vpath)
                if not cap.isOpened():
                    print(f"  Warning: cannot open {vpath!r}, skipping")
                    open_videos[key] = (None, None)
                else:
                    open_videos[key] = (cap, cap.get(cv2.CAP_PROP_FPS))

            cap, fps = open_videos[key]
            if cap is None:
                continue

            if time_unit == "seconds":
                begin_frame = round(seg["begin"] * fps)
                end_frame   = round(seg["end"]   * fps)
            else:
                begin_frame = round(seg["begin"])
                end_frame   = round(seg["end"])

            step  = max(1, round(fps / SAMPLE_FPS))
            label = 1 if seg["segment_type"] == "c" else 0

            frame_num = begin_frame
            while frame_num <= end_frame:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                ret, frame = cap.read()
                if not ret:
                    break
                small = cv2.resize(frame, (frame_w, frame_h))
                hsv   = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                X_rows.append(hsv.flatten().astype(np.float32))
                y_rows.append(label)
                frame_num += step
    finally:
        for cap, _ in open_videos.values():
            if cap is not None:
                cap.release()

    if not X_rows:
        return np.empty((0, frame_w * frame_h * 3), dtype=np.float32), np.empty(0, dtype=np.int32)

    return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.int32)


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def _load_sample_frame(segments, video_dir, time_unit):
    """Grab one mid-segment frame for use as a reference in the importance map."""
    for seg in segments:
        vpath = os.path.join(video_dir, seg["filename"] + ".mp4")
        cap   = cv2.VideoCapture(vpath)
        if not cap.isOpened():
            continue
        fps       = cap.get(cv2.CAP_PROP_FPS)
        mid_t     = (seg["begin"] + seg["end"]) / 2
        mid_frame = round(mid_t * fps if time_unit == "seconds" else mid_t)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, mid_frame))
        ret, frame = cap.read()
        cap.release()
        if ret:
            return frame
    return None


def visualize_importance(model, sample_frame, output_path):
    """
    Save a three-panel importance map:
      Left  — reference video frame (spatial context)
      Middle — heatmap of which pixels separate the two classes most
               reliably (hot = highest Fisher score, used in classifier)
      Right  — overlay of both

    Brighter / warmer regions in the heatmap are the pixel positions the
    classifier relies on most heavily to separate commercial from non-commercial.
    """
    frame_h = model["frame_h"]
    frame_w = model["frame_w"]
    n_feat  = frame_h * frame_w * 3

    # Reconstruct full-resolution Fisher score map
    all_scores = np.zeros(n_feat, dtype=np.float32)
    all_scores[model["pixel_indices"]] = model["fisher_scores"].astype(np.float32)

    # Sum H + S + V contributions per spatial location → 2D map
    importance_2d = all_scores.reshape(frame_h, frame_w, 3).sum(axis=2)
    importance_2d /= importance_2d.max() + 1e-10

    # Upscale with nearest-neighbour so pixel boundaries stay crisp
    scale   = 8
    vis_h   = frame_h * scale
    vis_w   = frame_w * scale
    imp_big = cv2.resize(importance_2d, (vis_w, vis_h), interpolation=cv2.INTER_NEAREST)
    heatmap = cv2.applyColorMap((imp_big * 255).astype(np.uint8), cv2.COLORMAP_HOT)

    if sample_frame is not None:
        ref  = cv2.resize(sample_frame, (vis_w, vis_h))
        over = cv2.addWeighted(ref, 0.45, heatmap, 0.55, 0)
        panels = np.hstack([ref, heatmap, over])
        labels = ["Reference frame", "Most informative pixels", "Overlay"]
    else:
        panels = heatmap
        labels = ["Most informative pixels (hot = more important)"]

    # Label bar above the panels
    bar_h   = 28
    bar     = np.full((bar_h, panels.shape[1], 3), 30, dtype=np.uint8)
    for i, lbl in enumerate(labels):
        x = i * vis_w + 6
        cv2.putText(bar, lbl, (x, bar_h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1, cv2.LINE_AA)

    cv2.imwrite(output_path, np.vstack([bar, panels]))
    print(f"Pixel importance map saved: {output_path}")


# ---------------------------------------------------------------------------
# Fisher separation score
# ---------------------------------------------------------------------------

def fisher_scores(X, y):
    """
    Per-pixel Fisher criterion: (mu_c - mu_n)^2 / (var_c + var_n + eps).

    High score means a pixel's HSV value reliably separates commercial from
    non-commercial frames across the training set.
    """
    mask_c = y == 1
    mask_n = y == 0

    if mask_c.sum() == 0 or mask_n.sum() == 0:
        raise ValueError("Training data must contain both commercial and non-commercial frames.")

    mu_c  = X[mask_c].mean(axis=0)
    mu_n  = X[mask_n].mean(axis=0)
    var_c = X[mask_c].var(axis=0)
    var_n = X[mask_n].var(axis=0)

    return (mu_c - mu_n) ** 2 / (var_c + var_n + 1e-6)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(csv_path, video_dir, network, time_unit, frame_w, frame_h, top_k, output_path):
    segments = load_segments(csv_path, network)
    if not segments:
        sys.exit("No segments found in CSV.")

    n_c = sum(1 for s in segments if s["segment_type"] == "c")
    n_n = len(segments) - n_c
    print(f"Segments: {n_c} commercial, {n_n} non-commercial")

    print("Extracting frames...")
    X, y = extract_frames(segments, video_dir, time_unit, frame_w, frame_h)

    if X.shape[0] == 0:
        sys.exit("No frames extracted — check video paths and CSV time-unit.")

    n_frames_c = int((y == 1).sum())
    n_frames_n = int((y == 0).sum())
    print(f"Frames extracted: {n_frames_c} commercial, {n_frames_n} non-commercial")

    # Find the pixel positions that best separate the two classes
    n_features = X.shape[1]
    actual_k   = min(top_k, n_features)
    print(f"Scoring {n_features} pixel channels, selecting top {actual_k}...")

    scores  = fisher_scores(X, y)
    top_idx = np.argsort(scores)[-actual_k:]  # highest Fisher scores last

    # Decode pixel positions for reporting
    n_channels = 3
    n_pixels   = frame_w * frame_h
    positions  = [(int(i // n_channels) // frame_w,
                   int(i // n_channels) %  frame_w,
                   int(i %  n_channels)) for i in top_idx]
    print(f"Top 5 most informative positions (row, col, channel): "
          f"{positions[-5:][::-1]}")

    # Train on the selected pixels only
    X_sel  = X[:, top_idx]
    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X_sel)

    clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
    clf.fit(X_sc, y)
    train_acc = clf.score(X_sc, y)
    print(f"Training accuracy: {train_acc:.3%}")

    model = {
        "classifier":    clf,
        "scaler":        scaler,
        "pixel_indices": top_idx,       # indices into a flattened (frame_h, frame_w, 3) HSV array
        "frame_w":       frame_w,
        "frame_h":       frame_h,
        "fisher_scores": scores[top_idx],
        "training_info": {
            "csv":               csv_path,
            "network":           network,
            "time_unit":         time_unit,
            "top_k":             actual_k,
            "n_frames_c":        n_frames_c,
            "n_frames_n":        n_frames_n,
            "training_accuracy": round(train_acc, 4),
        },
    }

    with open(output_path, "wb") as f:
        pickle.dump(model, f)

    print(f"Model saved: {output_path}")
    print(f"  Frame size:            {frame_w}x{frame_h}")
    print(f"  Pixels used:           {actual_k} of {n_features}")
    print(f"  Training accuracy:     {train_acc:.3%}")

    vis_path    = os.path.splitext(output_path)[0] + "_importance.png"
    sample_frame = _load_sample_frame(segments, video_dir, time_unit)
    visualize_importance(model, sample_frame, vis_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train a pixel-based commercial frame classifier from GT data."
    )
    parser.add_argument("--csv",        required=True,
                        help="Path to ground truth CSV file")
    parser.add_argument("--videos",     required=True,
                        help="Directory containing video files")
    parser.add_argument("--network",    required=True,
                        help="Network suffix appended to filenames (e.g. CNN, FNC)")
    parser.add_argument("--time-unit",  default="frames", choices=["frames", "seconds"],
                        help="Whether CSV times are frame numbers or seconds (default: frames)")
    parser.add_argument("--frame-size", default=f"{DEFAULT_FRAME_W}x{DEFAULT_FRAME_H}",
                        help=f"WxH to resize frames for feature extraction "
                             f"(default: {DEFAULT_FRAME_W}x{DEFAULT_FRAME_H})")
    parser.add_argument("--top-k",      default=DEFAULT_TOP_K, type=int,
                        help=f"Number of the most informative pixel channels to use "
                             f"(default: {DEFAULT_TOP_K})")
    parser.add_argument("--output",     default=DEFAULT_OUTPUT,
                        help=f"Output model path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    try:
        w_str, h_str = args.frame_size.lower().split("x")
        frame_w, frame_h = int(w_str), int(h_str)
    except ValueError:
        sys.exit("--frame-size must be WxH, e.g. 64x36")

    train(args.csv, args.videos, args.network, args.time_unit,
          frame_w, frame_h, args.top_k, args.output)
