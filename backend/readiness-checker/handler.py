"""
SegmentationReadinessChecker — Ensures both transcript and commercial
detection results exist before triggering AI segmentation.

This Lambda is invoked when:
  - A transcript arrives in the transcription bucket
  - Commercial detection results arrive in the video bucket

It checks for the existence of BOTH inputs for the given video.
Only when both are confirmed does it trigger AI segmentation.
"""

import json
import os
import boto3

s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

VIDEO_BUCKET = os.environ.get('VIDEO_BUCKET', '')
TRANSCRIPTION_BUCKET = os.environ.get('TRANSCRIPTION_BUCKET', '')
SEGMENTATION_FUNCTION = os.environ.get('SEGMENTATION_FUNCTION', '')
COMMERCIAL_PREFIX = os.environ.get('COMMERCIAL_RESULTS_PREFIX', 'commercial_results/')


def get_video_base_name(key):
    """Extract base video name (e.g. 20240801MSNBC) from transcript or result key."""
    filename = os.path.basename(key).replace('.json', '').replace('.csv', '')
    # Strip known suffixes
    for suffix in ['_segments', '_with_teasers', '_evaluation']:
        if filename.endswith(suffix):
            filename = filename[:-len(suffix)]
            break
    # Strip timestamp suffix like -1762983519
    parts = filename.rsplit('-', 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 8:
        return parts[0]
    return filename


def handler(event, context):
    """Check if both transcript and commercial results exist for this video."""
    print(f"Readiness check: {json.dumps(event, default=str)}")

    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        video_name = get_video_base_name(key)

        if not video_name:
            print(f"Could not extract video name from {key}")
            continue

        print(f"Checking readiness for: {video_name}")

        # Check if commercial results exist
        commercial_key = f'{COMMERCIAL_PREFIX}{video_name}_segments.json'
        commercial_ready = False
        try:
            s3.head_object(Bucket=VIDEO_BUCKET, Key=commercial_key)
            commercial_ready = True
            print(f"  ✓ Commercial results found: {commercial_key}")
        except Exception:
            print(f"  ✗ Commercial results NOT found: {commercial_key}")

        # Check if transcript exists
        transcript_ready = False
        try:
            resp = s3.list_objects_v2(
                Bucket=TRANSCRIPTION_BUCKET, Prefix=video_name, MaxKeys=5
            )
            for obj in resp.get('Contents', []):
                if obj['Key'].endswith('.json'):
                    transcript_ready = True
                    print(f"  ✓ Transcript found: {obj['Key']}")
                    break
            if not transcript_ready:
                print(f"  ✗ Transcript NOT found for {video_name}")
        except Exception as e:
            print(f"  ✗ Error checking transcript: {e}")

        # If both ready, trigger AI segmentation
        if commercial_ready and transcript_ready:
            # Find the actual transcript key
            transcript_key = None
            resp = s3.list_objects_v2(
                Bucket=TRANSCRIPTION_BUCKET, Prefix=video_name, MaxKeys=5
            )
            for obj in resp.get('Contents', []):
                if obj['Key'].endswith('.json'):
                    transcript_key = obj['Key']
                    break

            if transcript_key:
                # Construct event pointing to the transcript (not the trigger source)
                segmentation_event = {
                    'Records': [{
                        's3': {
                            'bucket': {'name': TRANSCRIPTION_BUCKET},
                            'object': {'key': transcript_key}
                        }
                    }]
                }
                print(f"  → Both inputs ready, triggering AI segmentation for {transcript_key}")
                lambda_client.invoke(
                    FunctionName=SEGMENTATION_FUNCTION,
                    InvocationType='Event',
                    Payload=json.dumps(segmentation_event),
                )
        else:
            print(f"  → Not ready yet (commercial={commercial_ready}, transcript={transcript_ready})")

    return {'statusCode': 200}
