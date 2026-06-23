"""
AI Segment Classifier — Lambda Handler

Uses Claude (via Amazon Bedrock) to classify transcript segments into
configurable types. Filters out visual boundary segments before analysis.

Customize: Modify SYSTEM_PROMPT and segment types for your use case.

Input event (S3 trigger from transcription bucket):
  Standard S3 notification with Records[].s3.bucket/object

Input event (direct invocation):
  {
    "transcription_bucket": "...",
    "transcription_key": "...",
    "boundary_bucket": "...",
    "boundary_key": "...",
    "output_bucket": "...",
    "output_prefix": "results/"
  }
"""

import json
import os
import boto3
from typing import Any

from models import ProcessedTranscript, TranscriptAnalysis
from timestamp_matcher import (
    generate_timestamps_for_segments,
    merge_consecutive_segments,
    prepare_word_items,
)
from pydantic_ai import Agent

s3 = boto3.client('s3')

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
BEDROCK_MODEL = f"bedrock:{os.environ.get('BEDROCK_MODEL_ID', 'us.anthropic.claude-sonnet-4-5-20250929-v1:0')}"

# Customize this prompt for your video type
SYSTEM_PROMPT = """
You are an expert video content analyst. Your task is to analyze transcripts and identify
distinct content segments.

NOTE: Visual boundaries (detected separately) have been removed and replaced with the marker
[ BOUNDARY ]. Each marker indicates a hard break — no segment should span across a [ BOUNDARY ] marker.

Segment Types (customize these for your use case):
- Content Segment: Main content (stories, presentations, interviews, lectures, etc.)
- Transition: Brief transitions between content segments

How to Identify Segments:
1. Look for topic changes, speaker transitions, or shifts in focus
2. [ BOUNDARY ] markers are hard boundaries between segments
3. Extract verbatim first and last sentences for timestamp matching

Important Guidelines:
- Do not create overlapping segments
- Ensure every part of the transcript is assigned to a segment
- Extract first_sentence and last_sentence VERBATIM from the transcript
"""
# ─────────────────────────────────────────────────────────────────────────────


def load_boundary_segments(bucket: str, key: str) -> list[tuple[float, float]]:
    """Load visual boundary timings from S3."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(response['Body'].read())
        return [
            (float(seg['start_time']), float(seg['end_time']))
            for seg in data.get('segments', [])
        ]
    except Exception as e:
        print(f"Error loading boundary segments: {e}")
        return []


def filter_transcript_with_boundaries(word_items: list[dict],
                                      boundaries: list[tuple[float, float]]) -> str:
    """Filter transcript to mark boundary regions."""
    def is_in_boundary(start: float | None, end: float | None) -> bool:
        if start is None and end is None:
            return False
        t = start if start is not None else end
        for break_start, break_end in boundaries:
            if break_start <= t <= break_end:
                return True
        return False

    parts = []
    in_boundary = False

    for item in word_items:
        start_time = item.get("start_time")
        end_time = item.get("end_time")
        has_timestamp = start_time is not None or end_time is not None

        if has_timestamp:
            if is_in_boundary(start_time, end_time):
                if not in_boundary:
                    parts.append("\n\n[ BOUNDARY ]\n\n")
                    in_boundary = True
                continue
            else:
                in_boundary = False

        if not in_boundary:
            parts.append(item["content"])

    return "".join(parts).strip()


def lambda_handler(event, context):
    """Lambda entry point."""
    try:
        # Determine input source
        if 'Records' in event:
            record = event['Records'][0]
            transcription_bucket = record['s3']['bucket']['name']
            transcription_key = record['s3']['object']['key']

            filename = transcription_key.split('/')[-1].replace('.json', '')
            base_filename = filename.split('-')[0]

            boundary_bucket = os.environ.get('BOUNDARY_BUCKET', '')
            boundary_key = f'visual_results/{base_filename}_segments.json'
            output_bucket = os.environ.get('OUTPUT_BUCKET')
            output_prefix = 'results/'
        else:
            transcription_bucket = event['transcription_bucket']
            transcription_key = event['transcription_key']
            boundary_bucket = event['boundary_bucket']
            boundary_key = event['boundary_key']
            output_bucket = event['output_bucket']
            output_prefix = event.get('output_prefix', 'results/')

        # Load transcription
        response = s3.get_object(Bucket=transcription_bucket, Key=transcription_key)
        transcript_data = json.loads(response['Body'].read())

        word_items = prepare_word_items(transcript_data)
        boundaries = load_boundary_segments(boundary_bucket, boundary_key)
        filtered_transcript = filter_transcript_with_boundaries(word_items, boundaries)

        # Run AI classification
        agent = Agent(model=BEDROCK_MODEL, system_prompt=SYSTEM_PROMPT)

        result = agent.run_sync(
            f"Analyze and segment this transcript:\n\n{filtered_transcript}",
            result_type=TranscriptAnalysis,
        )

        segments = result.output.segments
        segments_with_timestamps = generate_timestamps_for_segments(word_items, segments)
        merged_segments = merge_consecutive_segments(segments_with_timestamps)

        # Write output
        filename = transcription_key.split('/')[-1]
        output_key = f"{output_prefix}{filename.replace('.json', '_segments.json')}"

        output_data = {
            "source_file": filename,
            "segment_count": len(merged_segments),
            "segments": [s.model_dump() for s in merged_segments],
        }

        s3.put_object(
            Bucket=output_bucket,
            Key=output_key,
            Body=json.dumps(output_data, indent=2),
            ContentType="application/json",
        )

        return {"statusCode": 200, "body": json.dumps({"segments": len(merged_segments)})}

    except Exception as e:
        print(f"Error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
