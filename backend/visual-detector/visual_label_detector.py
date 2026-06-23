"""
Visual Label Detector — Lambda handler for profile-based video segment detection.

Triggered with video metadata. Downloads the video, loads the appropriate
color profile from S3 based on a configurable source identifier, runs
visual label detection, and writes segment results to S3.
"""

import json
import os
import tempfile
import boto3
import numpy as np

from detect_segments import find_segments, cleanup

s3 = boto3.client('s3')

VIDEO_BUCKET = os.environ.get('VIDEO_BUCKET', '')
PROFILES_PREFIX = os.environ.get('PROFILES_PREFIX', 'config/profiles/')
DETECTION_CONFIG_KEY = os.environ.get('DETECTION_CONFIG_KEY', 'config/detection_config.json')

# Default detection configurations (used if S3 config not found)
DEFAULT_CONFIGS = {
    "default": {
        "crop_top_fraction": 0.75,
        "crop_bottom_fraction": 1.0,
        "crop_left_fraction": 0.0,
        "crop_right_fraction": 1.0,
        "chi_square_threshold": 0.35,
        "scan_fps": 1,
        "profile_key": "config/profiles/default_profile.npy"
    }
}


def extract_source_id(video_name: str) -> str | None:
    """Extract source identifier from video filename.

    Assumes the naming convention: {date_or_prefix}{SourceID}
    e.g. '20240801ABC' -> 'ABC', 'lecture_series_01' -> None
    """
    i = len(video_name) - 1
    while i >= 0 and video_name[i].isalpha():
        i -= 1
    suffix = video_name[i + 1:]
    return suffix.upper() if suffix else None


def load_detection_config(source_id: str) -> dict:
    """Load detection configuration from S3 or fall back to defaults.

    The config JSON in S3 maps source identifiers to detection parameters
    (crop regions, thresholds, profile paths). If no source-specific config
    is found, falls back to "default".
    """
    try:
        response = s3.get_object(Bucket=VIDEO_BUCKET, Key=DETECTION_CONFIG_KEY)
        configs = json.loads(response['Body'].read())
        if source_id in configs:
            return configs[source_id]
        if 'default' in configs:
            return configs['default']
    except Exception as e:
        print(f"Could not load detection config from S3: {e}, using built-in defaults")

    return DEFAULT_CONFIGS.get(source_id, DEFAULT_CONFIGS.get('default', {}))


def load_profile(profile_key: str) -> np.ndarray:
    """Download and load a .npy color profile from S3."""
    with tempfile.NamedTemporaryFile(suffix='.npy', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        s3.download_file(VIDEO_BUCKET, profile_key, tmp_path)
        profile = np.load(tmp_path).astype(np.float64)
        return profile
    finally:
        os.unlink(tmp_path)


def handler(event, context):
    """Lambda entry point for visual label detection.

    Expected event:
    {
        "s3_bucket": "my-videos-bucket",
        "s3_key": "video/20240801SOURCE.mp4",
        "video_name": "20240801SOURCE"
    }
    """
    print(f"Event: {json.dumps(event, default=str)}")

    video_name = event.get('video_name', '')
    s3_bucket = event.get('s3_bucket', VIDEO_BUCKET)
    s3_key = event.get('s3_key', f'video/{video_name}.mp4')

    # Derive video_name from s3_key if not provided
    if not video_name and s3_key:
        video_name = os.path.basename(s3_key).replace('.mp4', '')

    # Extract source identifier for config lookup
    source_id = extract_source_id(video_name)
    if not source_id:
        # No source suffix found — use "default" config
        source_id = 'default'
        print(f"No source ID extracted from '{video_name}', using default config")

    print(f"Source ID: {source_id} for video: {video_name}")

    # Load detection config
    config = load_detection_config(source_id)
    if not config:
        print(f"No configuration found for source: {source_id}")
        return {
            'video': video_name,
            'segments_count': 0,
            'result_key': '',
            'error': f'No configuration for source {source_id}'
        }

    # Load color profile
    profile_key = config.get('profile_key', f'{PROFILES_PREFIX}{source_id}_profile.npy')
    try:
        profile = load_profile(profile_key)
    except Exception as e:
        print(f"Failed to load profile {profile_key}: {e}")
        return {
            'video': video_name,
            'segments_count': 0,
            'result_key': '',
            'error': f'Profile not found: {profile_key}'
        }

    # Download video to /tmp
    video_path = f'/tmp/{video_name}.mp4'
    print(f"Downloading s3://{s3_bucket}/{s3_key} to {video_path}")

    try:
        threshold = config.get('chi_square_threshold', 0.35)
        scan_fps = config.get('scan_fps', 1)
        crop_config = {
            'crop_top_fraction': config.get('crop_top_fraction', 0.75),
            'crop_bottom_fraction': config.get('crop_bottom_fraction', 1.0),
            'crop_left_fraction': config.get('crop_left_fraction', 0.0),
            'crop_right_fraction': config.get('crop_right_fraction', 1.0),
        }

        try:
            s3.download_file(s3_bucket, s3_key, video_path)
            file_size = os.path.getsize(video_path)
            print(f"Downloaded {file_size} bytes to {video_path}")
            video_source = video_path
        except OSError as e:
            # /tmp full — fall back to presigned URL streaming
            print(f"Download failed ({e}), falling back to presigned URL")
            video_source = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': s3_bucket, 'Key': s3_key},
                ExpiresIn=900
            )

        segments, video_duration = find_segments(
            video_source, profile, threshold=threshold,
            scan_fps=scan_fps, crop_config=crop_config
        )
        segments = cleanup(segments, video_duration)
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)

    # Build output
    result = {
        "video": video_name,
        "segments": segments,
        "transition_events": []
    }

    # Write to S3
    result_key = f'segment_results/{video_name}_segments.json'
    s3.put_object(
        Bucket=s3_bucket,
        Key=result_key,
        Body=json.dumps(result, indent=2),
        ContentType='application/json'
    )
    print(f"Written {len(segments)} segments to s3://{s3_bucket}/{result_key}")

    return {
        'video': video_name,
        'segments_count': len(segments),
        'result_key': result_key
    }
