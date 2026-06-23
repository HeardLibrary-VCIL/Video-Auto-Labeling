import csv
import os
import cv2
import numpy as np

CSV_PATH = "ground_truth/fnc_ground_truth.csv"
VIDEO_DIR = "fnc_videos"
TARGET_TYPES = {"p", "n", "t"}

# Histogram parameters — HSV, fixed bins and ranges for all frames
H_BINS = 36
S_BINS = 32
V_BINS = 32
HIST_SIZE = [H_BINS, S_BINS, V_BINS]
HIST_RANGES = [0, 180, 0, 256, 0, 256]  # H: 0-180, S: 0-256, V: 0-256
CHANNELS = [0, 1, 2]


def load_segments(csv_path):
    segments = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["SegmentType"] in TARGET_TYPES:
                segments.append({
                    "filename": row["Filename"][0:8] + "FNC",
                    "segment_type": row["SegmentType"],
                    "begin": int(row["BeginTime"]),
                    "end": int(row["EndTime"]),
                })
    return segments


def compute_frame_histogram(frame):
    height = frame.shape[0]
    crop = frame[2 * height // 3 :, :, :]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], CHANNELS, None, HIST_SIZE, HIST_RANGES)
    cv2.normalize(hist, hist, alpha=1.0, beta=0.0, norm_type=cv2.NORM_L1)
    return hist.flatten()


PROFILE_PATH = "color_profile.npy"


def build_color_profile(csv_path=CSV_PATH, video_dir=VIDEO_DIR, output_path=PROFILE_PATH):
    segments = load_segments(csv_path)
    if not segments:
        raise ValueError("No segments with SegmentType p, n, or t found in CSV.")

    all_histograms = []
    open_videos = {}

    try:
        for seg in segments:
            video_path = os.path.join(video_dir, seg["filename"] + ".mp4")
            if seg["filename"] not in open_videos:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    print(f"Warning: cannot open video '{video_path}', skipping.")
                    open_videos[seg["filename"]] = (None, None)
                else:
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    open_videos[seg["filename"]] = (cap, fps)
            cap, fps = open_videos[seg["filename"]]
            if cap is None:
                continue

            begin_frame = seg["begin"]
            end_frame = seg["end"]
            step = max(1, round(fps))  # 1 FPS → advance by ~fps frames each step

            frame_num = begin_frame
            while frame_num <= end_frame:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                ret, frame = cap.read()
                if not ret:
                    break
                hist = compute_frame_histogram(frame)
                all_histograms.append(hist)
                frame_num += step
    finally:
        for cap, _ in open_videos.values():
            if cap is not None:
                cap.release()

    if not all_histograms:
        raise RuntimeError("No frames were successfully read from the selected segments.")

    histograms = np.stack(all_histograms, axis=0)  # shape: (n_frames, n_bins)
    median_histogram = np.median(histograms, axis=0)

    total = median_histogram.sum()
    if total > 0:
        median_histogram = median_histogram / total

    np.save(output_path, median_histogram)
    return median_histogram


if __name__ == "__main__":
    profile = build_color_profile()
    print(f"Saved to: {PROFILE_PATH}")
    print(f"Median color profile shape: {profile.shape}")
    print(f"Sum (should be 1.0): {profile.sum():.6f}")
    print(f"Non-zero bins: {np.count_nonzero(profile)}")
