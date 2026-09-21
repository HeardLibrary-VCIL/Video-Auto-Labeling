"""
Lambda handler for ticker-based commercial segment detection.

Triggered via Step Functions with video metadata. Downloads the video,
loads the network-specific trained model from S3, runs ticker detection,
and writes results to S3 in the same format as the black-frame detector.

If no trained model is available for the video's network — unknown network,
no config entry, or a missing .pkl — the video is handed to the black-frame
detector instead. The same happens when a model runs but finds nothing.
"""

import json
import os
import pickle
import tempfile
import boto3

from detect_segments import find_segments, cleanup

s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

VIDEO_BUCKET = os.environ.get('VIDEO_BUCKET', '')
PROFILES_PREFIX = os.environ.get('PROFILES_PREFIX', 'config/profiles/')
NETWORK_CONFIG_KEY = os.environ.get('NETWORK_CONFIG_KEY', 'config/ticker_network_config.json')
BLACKFRAME_LAUNCHER = os.environ.get('BLACKFRAME_LAUNCHER', '')

# Default network configurations (used if S3 config not found)
DEFAULT_CONFIGS = {
    "CNN": {
        "model_threshold": 0.5,
        "scan_fps": 0.5,
        "profile_key": "config/profiles/CNN_model.pkl"
    },
    "FNC": {
        "model_threshold": 0.5,
        "scan_fps": 0.5,
        "profile_key": "config/profiles/FNC_model.pkl"
    }
}


def extract_network(video_name: str) -> str | None:
    """Extract network identifier from video filename.
    
    Examples: '20240801CNN' -> 'CNN', '20240801FNC' -> 'FNC'
    """
    # Find trailing alphabetic characters
    i = len(video_name) - 1
    while i >= 0 and video_name[i].isalpha():
        i -= 1
    suffix = video_name[i + 1:]
    return suffix.upper() if suffix else None


def load_network_config(network: str) -> dict:
    """Load network configuration from S3 or use defaults."""
    try:
        response = s3.get_object(Bucket=VIDEO_BUCKET, Key=NETWORK_CONFIG_KEY)
        configs = json.loads(response['Body'].read())
        if network in configs:
            return configs[network]
    except Exception as e:
        print(f"Could not load network config from S3: {e}, using defaults")
    
    return DEFAULT_CONFIGS.get(network, {})


def load_model(profile_key: str) -> dict:
    """Download and load a trained .pkl model from S3.

    The model is a dict with keys: frame_w, frame_h, pixel_indices,
    scaler, classifier, and (optionally) training_info.
    """
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        s3.download_file(VIDEO_BUCKET, profile_key, tmp_path)
        with open(tmp_path, 'rb') as f:
            model = pickle.load(f)
        return model
    finally:
        os.unlink(tmp_path)


def blackframe_fallback(s3_bucket: str, s3_prefix: str, video_name: str, reason: str) -> dict:
    """Hand a video off to the black-frame detector.

    Used when no trained model is available for the video's network, and when
    a model ran but found no segments. The launcher starts the Step Functions
    batch, which writes its own results to segment_results/.
    """
    if not BLACKFRAME_LAUNCHER:
        print(f"{reason}; no black-frame launcher configured, giving up")
        return {
            'video': video_name,
            'segments_count': 0,
            'result_key': '',
            'error': reason
        }

    print(f"{reason}; falling back to black-frame detection for {video_name}")
    lambda_client.invoke(
        FunctionName=BLACKFRAME_LAUNCHER,
        InvocationType='Event',
        Payload=json.dumps({
            's3_bucket': s3_bucket,
            's3_prefix': s3_prefix,
            'video_names': [video_name]
        }),
    )
    return {
        'video': video_name,
        'segments_count': 0,
        'result_key': '',
        'fallback': 'black-frame',
        'message': reason
    }


def handler(event, context):
    """Lambda entry point.

    Expected event:
    {
        "s3_bucket": "tvnews-videos-...",
        "s3_key": "video/20240801CNN.mp4",
        "video_name": "20240801CNN"
    }
    """
    print(f"Event: {json.dumps(event, default=str)}")

    video_name = event.get('video_name', '')
    s3_bucket = event.get('s3_bucket', VIDEO_BUCKET)
    s3_key = event.get('s3_key', f'video/{video_name}.mp4')

    # Derive video_name from s3_key if not provided
    if not video_name and s3_key:
        video_name = os.path.basename(s3_key).replace('.mp4', '')

    # Directory the black-frame launcher should scan if we fall back
    key_dir = os.path.dirname(s3_key)
    s3_prefix = f'{key_dir}/' if key_dir else 'video/'

    # Extract network
    network = extract_network(video_name)
    if not network:
        return blackframe_fallback(
            s3_bucket, s3_prefix, video_name,
            f'Could not extract network from {video_name}'
        )

    print(f"Detected network: {network} for video: {video_name}")

    # Load network config
    config = load_network_config(network)
    if not config:
        return blackframe_fallback(
            s3_bucket, s3_prefix, video_name,
            f'No model configured for network {network}'
        )

    # Load trained model
    profile_key = config.get('profile_key', f'{PROFILES_PREFIX}{network}_model.pkl')
    try:
        model = load_model(profile_key)
    except Exception as e:
        print(f"Failed to load model {profile_key}: {e}")
        return blackframe_fallback(
            s3_bucket, s3_prefix, video_name,
            f'No trained model for network {network} at {profile_key}'
        )

    # Download video to /tmp (faster than streaming for full-hour videos)
    # Fall back to presigned URL if /tmp runs out of space
    video_path = f'/tmp/{video_name}.mp4'
    print(f"Downloading s3://{s3_bucket}/{s3_key} to {video_path}")

    try:
        threshold = config.get('model_threshold', 0.5)
        scan_fps = config.get('scan_fps', 1)

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
            video_source, model, threshold=threshold, scan_fps=scan_fps
        )
        segments = cleanup(segments, video_duration)
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)

    # Build output (same format as black-frame detector)
    result = {
        "video": video_name,
        "segments": segments,
        "transition_events": []  # model detector doesn't produce these
    }

    # Fallback: if the model found no segments, try the black-frame method
    if len(segments) == 0 and BLACKFRAME_LAUNCHER:
        return blackframe_fallback(
            s3_bucket, s3_prefix, video_name,
            f'Model found 0 segments for {video_name}'
        )

    # Write to S3 — downstream readiness check and results merger read this prefix
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
