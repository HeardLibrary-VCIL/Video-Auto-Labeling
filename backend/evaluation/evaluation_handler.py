"""
Evaluation Lambda — compares predicted segments against ground truth.

Ground truth CSV format (minimum required columns):
  filename, segment_type, segment_start, segment_end

Predictions JSON format (from ai_results/ or subsegment_results/):
  {"segments": [{"segment_type": "...", "segment_start": ..., "segment_end": ...}]}

Metrics computed:
  - Frame-level accuracy (per-second classification)
  - Segment F1 at multiple IoU thresholds (0.25, 0.5, 0.75)
  - Boundary F1 at multiple tolerances (2s, 5s, 10s)
  - Mean boundary error (seconds)

Triggered by S3 event or direct invocation with video_name.
"""

import json
import os
import csv
import boto3
from dataclasses import dataclass
from pathlib import Path
import tempfile

s3 = boto3.client('s3')

PROCESSING_BUCKET = os.environ.get('OUTPUT_BUCKET', os.environ.get('PROCESSING_BUCKET', ''))
GROUND_TRUTH_BUCKET = os.environ.get('GROUND_TRUTH_BUCKET', PROCESSING_BUCKET)
GROUND_TRUTH_KEY = os.environ.get('GROUND_TRUTH_KEY', 'ground_truth/ground_truth.csv')
PREDICTIONS_BUCKET = os.environ.get('SEGMENTATION_BUCKET', PROCESSING_BUCKET)
PREDICTIONS_PREFIX = os.environ.get('PREDICTIONS_PREFIX', 'ai_results/')


@dataclass
class Segment:
    segment_type: str
    start: float
    end: float


# ─── GROUND TRUTH & PREDICTION LOADING ───────────────────────────────────────

def load_ground_truth(csv_path: str, target_filename: str) -> list[Segment]:
    """Load ground truth segments for a specific file from CSV.

    CSV must have columns: filename, segment_type, segment_start, segment_end
    Additional columns are ignored.
    """
    segments = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            fn = row.get('filename', row.get('file', '')).strip()
            if fn != target_filename:
                continue
            seg_type = row.get('segment_type', '').strip().lower()
            try:
                start = float(row.get('segment_start', 0))
                end = float(row.get('segment_end', 0))
            except (ValueError, TypeError):
                continue
            if start < end:
                segments.append(Segment(segment_type=seg_type, start=start, end=end))

    segments.sort(key=lambda s: s.start)
    return segments


def load_predictions(json_path: str) -> list[Segment]:
    """Load predicted segments from a JSON file."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    segments = []
    for seg in data.get('segments', []):
        seg_type = str(seg.get('segment_type', '')).strip().lower()
        try:
            start = float(seg.get('segment_start', seg.get('start_time', 0)))
            end = float(seg.get('segment_end', seg.get('end_time', 0)))
        except (ValueError, TypeError):
            continue
        if start < end:
            segments.append(Segment(segment_type=seg_type, start=start, end=end))

    segments.sort(key=lambda s: s.start)
    return segments


# ─── METRICS ──────────────────────────────────────────────────────────────────

def compute_frame_accuracy(gt_segments: list[Segment], pred_segments: list[Segment]) -> dict:
    """Compute per-second frame-level classification accuracy."""
    if not gt_segments:
        return {'accuracy': 0.0, 'total_seconds': 0}

    max_time = max(s.end for s in gt_segments + pred_segments) if pred_segments else max(s.end for s in gt_segments)
    total_seconds = int(max_time) + 1

    correct = 0
    for t in range(total_seconds):
        gt_type = None
        pred_type = None
        for s in gt_segments:
            if s.start <= t < s.end:
                gt_type = s.segment_type
                break
        for s in pred_segments:
            if s.start <= t < s.end:
                pred_type = s.segment_type
                break
        if gt_type == pred_type:
            correct += 1

    return {
        'accuracy': round(correct / total_seconds, 4) if total_seconds > 0 else 0.0,
        'total_seconds': total_seconds,
        'correct_seconds': correct,
    }


def compute_iou(seg_a: Segment, seg_b: Segment) -> float:
    """Compute Intersection over Union between two segments."""
    overlap_start = max(seg_a.start, seg_b.start)
    overlap_end = min(seg_a.end, seg_b.end)
    intersection = max(0, overlap_end - overlap_start)

    union = (seg_a.end - seg_a.start) + (seg_b.end - seg_b.start) - intersection
    return intersection / union if union > 0 else 0.0


def compute_segment_f1(gt_segments: list[Segment], pred_segments: list[Segment],
                       iou_thresholds: list[float] = [0.25, 0.5, 0.75]) -> dict:
    """Compute segment detection F1 at multiple IoU thresholds."""
    results = {}
    for threshold in iou_thresholds:
        matched_gt = set()
        matched_pred = set()

        for i, pred in enumerate(pred_segments):
            best_iou = 0.0
            best_gt_idx = -1
            for j, gt in enumerate(gt_segments):
                if j in matched_gt:
                    continue
                # Only match same type
                if gt.segment_type != pred.segment_type:
                    continue
                iou = compute_iou(gt, pred)
                if iou > best_iou:
                    best_iou = iou
                    best_gt_idx = j

            if best_iou >= threshold and best_gt_idx >= 0:
                matched_gt.add(best_gt_idx)
                matched_pred.add(i)

        tp = len(matched_pred)
        precision = tp / len(pred_segments) if pred_segments else 0.0
        recall = tp / len(gt_segments) if gt_segments else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        results[str(threshold)] = {
            'f1': round(f1, 4),
            'precision': round(precision, 4),
            'recall': round(recall, 4),
            'true_positives': tp,
        }

    return results


def compute_boundary_f1(gt_segments: list[Segment], pred_segments: list[Segment],
                        tolerances: list[float] = [2.0, 5.0, 10.0]) -> dict:
    """Compute boundary detection F1 at multiple time tolerances."""
    gt_boundaries = set()
    for s in gt_segments:
        gt_boundaries.add(s.start)
        gt_boundaries.add(s.end)

    pred_boundaries = set()
    for s in pred_segments:
        pred_boundaries.add(s.start)
        pred_boundaries.add(s.end)

    gt_list = sorted(gt_boundaries)
    pred_list = sorted(pred_boundaries)

    results = {}
    for tolerance in tolerances:
        matched_gt = set()
        tp = 0

        for pb in pred_list:
            for i, gb in enumerate(gt_list):
                if i in matched_gt:
                    continue
                if abs(pb - gb) <= tolerance:
                    tp += 1
                    matched_gt.add(i)
                    break

        precision = tp / len(pred_list) if pred_list else 0.0
        recall = tp / len(gt_list) if gt_list else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        results[f"{tolerance}s"] = {
            'f1': round(f1, 4),
            'precision': round(precision, 4),
            'recall': round(recall, 4),
        }

    return results


def compute_mean_boundary_error(gt_segments: list[Segment], pred_segments: list[Segment]) -> float:
    """Compute mean absolute error of matched boundaries."""
    gt_boundaries = sorted(set(s.start for s in gt_segments) | set(s.end for s in gt_segments))
    pred_boundaries = sorted(set(s.start for s in pred_segments) | set(s.end for s in pred_segments))

    if not gt_boundaries or not pred_boundaries:
        return 0.0

    errors = []
    used_pred = set()
    for gb in gt_boundaries:
        best_err = float('inf')
        best_idx = -1
        for i, pb in enumerate(pred_boundaries):
            if i in used_pred:
                continue
            err = abs(pb - gb)
            if err < best_err:
                best_err = err
                best_idx = i
        if best_idx >= 0 and best_err < 60:  # only count if within 60s
            errors.append(best_err)
            used_pred.add(best_idx)

    return round(sum(errors) / len(errors), 2) if errors else 0.0


# ─── LAMBDA HANDLER ───────────────────────────────────────────────────────────

def get_video_base_name(key: str) -> str:
    """Extract base video name from an S3 key."""
    filename = os.path.basename(key).replace('.json', '').replace('.csv', '')
    for suffix in ['_segments', '_with_subsegments', '_evaluation']:
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
            break
    # Strip timestamp suffix like -1762983519
    parts = filename.rsplit('-', 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 8:
        filename = parts[0]
    return filename


def lambda_handler(event, context):
    """Evaluate segmentation predictions against ground truth.

    Triggered by:
    1. S3 event (Records format) — extracts video_name from key
    2. Direct invocation with {"video_name": "..."}
    """
    try:
        # Determine video name
        if 'Records' in event:
            record = event['Records'][0]
            trigger_key = record['s3']['object']['key']
            video_name = get_video_base_name(trigger_key)
        elif 'video_name' in event:
            video_name = event['video_name']
        else:
            return {'statusCode': 400, 'body': json.dumps({'error': 'No video_name or Records in event'})}

        print(f"Evaluating: {video_name}")

        gt_bucket = event.get('ground_truth_bucket', GROUND_TRUTH_BUCKET)
        gt_key = event.get('ground_truth_key', GROUND_TRUTH_KEY)
        pred_bucket = event.get('predictions_bucket', PREDICTIONS_BUCKET)
        pred_prefix = event.get('predictions_prefix', PREDICTIONS_PREFIX)
        output_bucket = event.get('output_bucket', PROCESSING_BUCKET)
        output_key = f'evaluation/{video_name}_evaluation.json'

        with tempfile.TemporaryDirectory() as tmpdir:
            # Download ground truth CSV
            gt_path = os.path.join(tmpdir, 'ground_truth.csv')
            try:
                resp = s3.get_object(Bucket=gt_bucket, Key=gt_key)
                with open(gt_path, 'wb') as f:
                    f.write(resp['Body'].read())
            except Exception as e:
                print(f"Ground truth not found: {gt_bucket}/{gt_key} — {e}")
                return {'statusCode': 404, 'body': json.dumps({'error': f'Ground truth not found: {gt_key}'})}

            # Load ground truth for this video
            gt_segments = load_ground_truth(gt_path, video_name)
            if not gt_segments:
                print(f"No ground truth segments for {video_name}")
                return {'statusCode': 200, 'body': json.dumps({
                    'message': f'No ground truth found for {video_name}',
                    'video': video_name
                })}

            # Find and download prediction file
            pred_path = None
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=pred_bucket, Prefix=pred_prefix):
                for obj in page.get('Contents', []):
                    obj_video = get_video_base_name(obj['Key'])
                    if obj_video == video_name and obj['Key'].endswith('.json'):
                        pred_file = os.path.join(tmpdir, 'predictions.json')
                        resp = s3.get_object(Bucket=pred_bucket, Key=obj['Key'])
                        with open(pred_file, 'wb') as f:
                            f.write(resp['Body'].read())
                        pred_path = pred_file
                        break
                if pred_path:
                    break

            if not pred_path:
                print(f"No predictions found for {video_name} in {pred_bucket}/{pred_prefix}")
                return {'statusCode': 200, 'body': json.dumps({
                    'message': f'No predictions found for {video_name}',
                    'video': video_name
                })}

            # Load predictions
            pred_segments = load_predictions(pred_path)

            # Compute metrics
            frame_metrics = compute_frame_accuracy(gt_segments, pred_segments)
            segment_f1 = compute_segment_f1(gt_segments, pred_segments)
            boundary_f1 = compute_boundary_f1(gt_segments, pred_segments)
            mean_boundary_error = compute_mean_boundary_error(gt_segments, pred_segments)

            results = {
                'video': video_name,
                'gt_segment_count': len(gt_segments),
                'pred_segment_count': len(pred_segments),
                'frame_accuracy': frame_metrics,
                'segment_f1': segment_f1,
                'boundary_f1': boundary_f1,
                'mean_boundary_error_seconds': mean_boundary_error,
            }

            # Write results to S3
            s3.put_object(
                Bucket=output_bucket,
                Key=output_key,
                Body=json.dumps(results, indent=2),
                ContentType='application/json',
            )
            print(f"Evaluation written to s3://{output_bucket}/{output_key}")

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Evaluation complete',
                    'video': video_name,
                    'output_key': output_key,
                    'frame_accuracy': frame_metrics['accuracy'],
                })
            }

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
