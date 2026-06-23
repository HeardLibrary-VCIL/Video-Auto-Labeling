"""
Transition Detector — Lambda Handler

Identifies transition segments (teasers, previews, recaps) within the
boundaries between main content segments.

Customize: Modify the detection prompt in prompts.py for your transition types.

Triggered by: S3 event when AI segmentation results land.
"""

import json
import os
import csv
import io
import re
import boto3
from typing import Optional

from src.bedrock_client import BedrockTransitionDetector
from src.timestamp_matcher import prepare_word_items, find_flexible_match

s3 = boto3.client("s3")

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
TRANSCRIPTION_BUCKET = os.environ.get("TRANSCRIPTION_BUCKET", "")
SEGMENTATION_BUCKET = os.environ.get("SEGMENTATION_BUCKET", "")


def get_transcription_data(file_id: str) -> Optional[dict]:
    """Find and load transcription JSON from S3."""
    try:
        response = s3.list_objects_v2(
            Bucket=TRANSCRIPTION_BUCKET, Prefix=file_id, MaxKeys=10
        )
        for obj in response.get("Contents", []):
            if obj["Key"].endswith(".json"):
                result = s3.get_object(Bucket=TRANSCRIPTION_BUCKET, Key=obj["Key"])
                return json.loads(result["Body"].read())
        return None
    except Exception as e:
        print(f"Error loading transcription for {file_id}: {e}")
        return None


def extract_transcript_text(data: dict, start_time: float, end_time: float) -> str:
    """Extract transcript text between timestamps."""
    items = data.get("results", {}).get("items", [])
    extracted_words = []
    recording = False

    for item in items:
        item_type = item.get("type")
        content = item.get("alternatives", [{}])[0].get("content", "")

        if item_type == "pronunciation":
            item_start = float(item.get("start_time", 0))
            if item_start >= end_time:
                break
            if item_start >= start_time:
                recording = True
                extracted_words.append(" " + content)
        elif item_type == "punctuation":
            if recording:
                extracted_words.append(content)

    return "".join(extracted_words).strip()


def handler(event, context):
    """Lambda entry point."""
    print(f"Event: {json.dumps(event, default=str)}")

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        process_file(bucket, key)

    return {"statusCode": 200, "body": "Transition detection complete"}


def process_file(bucket: str, key: str):
    """Process a single segmentation result file."""
    print(f"Processing: s3://{bucket}/{key}")

    # Load segmentation results
    response = s3.get_object(Bucket=bucket, Key=key)
    data = json.loads(response["Body"].read())
    segments = data.get("segments", [])

    if not segments:
        print(f"No segments found in {key}")
        return

    # Extract video base name
    filename = os.path.basename(key)
    video_name = filename.replace("_segments.json", "").split("-")[0]

    # Load transcription
    transcript_data = get_transcription_data(video_name)
    if not transcript_data:
        print(f"No transcription found for {video_name}")
        return

    word_items = prepare_word_items(transcript_data)
    detector = BedrockTransitionDetector()

    # Detect transitions at content→break boundaries
    transition_segments = []
    for i, seg in enumerate(segments):
        if seg.get("segment_type", "").lower() in ("content", "n", "news segment"):
            if i + 1 < len(segments):
                next_seg = segments[i + 1]
                if next_seg.get("segment_type", "").lower() in ("break", "c", "commercial"):
                    # Extract transcript near the boundary
                    seg_start = float(seg.get("segment_start", seg.get("start_time", 0)))
                    break_start = float(next_seg.get("segment_start", next_seg.get("start_time", 0)))

                    # Look at the last portion of the content segment
                    search_start = max(seg_start, break_start - 90.0)
                    transcript = extract_transcript_text(transcript_data, search_start, break_start)

                    if transcript:
                        result, _, _, _, _ = detector.analyze_segment(transcript)
                        if result.has_transition and result.transition_text:
                            # Find timestamps
                            start_time, _ = find_flexible_match(
                                words=word_items, sentence=result.transition_text,
                                start_idx=0, is_start=True
                            )
                            transition_segments.append({
                                "start_time": start_time or break_start,
                                "end_time": break_start,
                                "label": "T",
                                "transcript": result.transition_text,
                            })

    # Write output
    output_key = f"transition_results/{video_name}_with_transitions.json"
    output_bucket = OUTPUT_BUCKET or bucket

    s3.put_object(
        Bucket=output_bucket,
        Key=output_key,
        Body=json.dumps({"video": video_name, "transitions": transition_segments}, indent=2),
        ContentType="application/json",
    )
    print(f"Output: s3://{output_bucket}/{output_key} ({len(transition_segments)} transitions)")
