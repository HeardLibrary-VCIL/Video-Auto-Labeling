"""
Results Merger — Combines all pipeline outputs into a unified JSON per video.

Merges: visual boundary segments + AI classification + transition detection + evaluation
Output: single JSON file readable by the frontend.

Triggered after transition detection completes.
"""

import json
import os
import boto3

s3 = boto3.client('s3')

VIDEO_BUCKET = os.environ['VIDEO_BUCKET']
PROCESSING_BUCKET = os.environ['PROCESSING_BUCKET']
OUTPUT_PREFIX = os.environ.get('OUTPUT_PREFIX', 'result/')


def get_video_base_name(key):
    """Extract base video name from various key formats."""
    filename = os.path.basename(key)
    for suffix in ['_segments.json', '_with_transitions.json', '_evaluation.json', '.json', '.csv']:
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
            break
    # Strip timestamp IDs like -1762983519
    parts = filename.rsplit('-', 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 8:
        filename = parts[0]
    return filename


def handler(event, context):
    """Merge all available results for a video."""
    print(f"Event: {json.dumps(event, default=str)}")

    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        process_trigger(bucket, key)

    return {'statusCode': 200}


def process_trigger(bucket, key):
    """Merge all available data for a video."""
    video_name = get_video_base_name(key)
    if not video_name:
        print(f"Could not extract video name from {key}")
        return

    print(f"Merging results for: {video_name}")

    all_segments = []

    # 1. Load visual boundary segments
    boundary_key = f'visual_results/{video_name}_segments.json'
    try:
        resp = s3.get_object(Bucket=VIDEO_BUCKET, Key=boundary_key)
        data = json.loads(resp['Body'].read())
        for seg in data.get('segments', []):
            all_segments.append({
                'segment_start': seg.get('start_time', 0),
                'segment_end': seg.get('end_time', 0),
                'segment_type': 'Break',
                'label': 'B',
                'title': 'Visual Boundary',
            })
    except Exception as e:
        print(f"No visual boundary results: {e}")

    # 2. Load AI segmentation results
    try:
        resp = s3.list_objects_v2(
            Bucket=PROCESSING_BUCKET, Prefix=f'results/{video_name}', MaxKeys=10
        )
        for obj in resp.get('Contents', []):
            if '_segments.json' in obj['Key']:
                data = json.loads(
                    s3.get_object(Bucket=PROCESSING_BUCKET, Key=obj['Key'])['Body'].read()
                )
                for seg in data.get('segments', []):
                    all_segments.append({
                        'segment_start': seg.get('segment_start', seg.get('start_time', 0)),
                        'segment_end': seg.get('segment_end', seg.get('end_time', 0)),
                        'segment_type': seg.get('segment_type', 'Content'),
                        'label': seg.get('label', 'C'),
                        'title': seg.get('title', ''),
                        'transcript': seg.get('first_sentence', ''),
                    })
                break
    except Exception as e:
        print(f"No AI segmentation results: {e}")

    # 3. Load transition detection results
    try:
        resp = s3.list_objects_v2(
            Bucket=PROCESSING_BUCKET, Prefix=f'transition_results/{video_name}', MaxKeys=10
        )
        for obj in resp.get('Contents', []):
            if '_with_transitions' in obj['Key']:
                data = json.loads(
                    s3.get_object(Bucket=PROCESSING_BUCKET, Key=obj['Key'])['Body'].read()
                )
                for seg in data.get('transitions', []):
                    all_segments.append({
                        'segment_start': seg.get('start_time', 0),
                        'segment_end': seg.get('end_time', 0),
                        'segment_type': 'Transition',
                        'label': 'T',
                        'title': 'Transition',
                        'transcript': seg.get('transcript', ''),
                    })
                break
    except Exception as e:
        print(f"No transition results: {e}")

    # 4. Sort by start time
    all_segments.sort(key=lambda s: float(s.get('segment_start', 0)))

    # 5. Write merged result
    output = {
        'video': video_name,
        'segments': all_segments,
    }

    output_key = f'{OUTPUT_PREFIX}{video_name}.json'
    s3.put_object(
        Bucket=VIDEO_BUCKET,
        Key=output_key,
        Body=json.dumps(output, indent=2),
        ContentType='application/json',
    )
    print(f"Merged: s3://{VIDEO_BUCKET}/{output_key} ({len(all_segments)} segments)")
