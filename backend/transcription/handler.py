"""
Transcription Lambda — Triggers AWS Transcribe on video upload.

Triggered by S3 event when a video is uploaded to video/*.mp4

Customize: Modify language code, speaker settings, or add custom vocabulary.
"""

import json
import boto3
import os
import time

s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
LANGUAGE_CODE = "en-US"          # Change for other languages
MAX_SPEAKERS = 15                # Maximum number of speakers to identify
SHOW_SPEAKER_LABELS = True       # Enable speaker diarization
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_FORMATS = {
    "wav": "wav", "mp3": "mp3", "mp4": "mp4",
    "mov": "mov", "flac": "flac", "ogg": "ogg", "webm": "webm"
}


def wait_for_upload(bucket_name, file_key, max_retries=20):
    """Wait for S3 upload to complete (multipart uploads)."""
    retries = 0
    while retries < max_retries:
        try:
            s3.head_object(Bucket=bucket_name, Key=file_key)
            return
        except Exception:
            retries += 1
            time.sleep(5)
    raise Exception(f"File {file_key} not fully uploaded after {max_retries} retries")


def get_media_format(file_key):
    extension = file_key.split('.')[-1].lower()
    return SUPPORTED_FORMATS.get(extension, "mp4")


def lambda_handler(event, context):
    for record in event["Records"]:
        try:
            bucket_name = record["s3"]["bucket"]["name"]
            file_key = record["s3"]["object"]["key"]
            job_id = file_key.split("/")[-1].split(".")[0]

            wait_for_upload(bucket_name, file_key)
            print(f"Starting transcription for: {file_key}")

            media_uri = f"s3://{bucket_name}/{file_key}"
            media_format = get_media_format(file_key)

            transcribe.start_transcription_job(
                TranscriptionJobName=job_id,
                Media={"MediaFileUri": media_uri},
                MediaFormat=media_format,
                LanguageCode=LANGUAGE_CODE,
                OutputBucketName=OUTPUT_BUCKET,
                Settings={
                    "ShowSpeakerLabels": SHOW_SPEAKER_LABELS,
                    "MaxSpeakerLabels": MAX_SPEAKERS,
                }
            )
            print(f"Transcription job started: {job_id}")
        except Exception as e:
            print(f"Error processing {file_key}: {e}")
