import boto3
import json
import os
from datetime import date
from botocore.config import Config


def _client():
    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{os.getenv('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(s3={"addressing_style": "path"}),
    )


def _key(target_date: date) -> str:
    return f"team-accountability/chat/{target_date.isoformat()}.json"


def load_chat(target_date: date) -> list:
    """Load chat history for a given date. Returns [] if none exists."""
    try:
        resp = _client().get_object(Bucket=os.getenv("BUCKET_NAME"), Key=_key(target_date))
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
        return []


def save_chat(target_date: date, history: list):
    """Save chat history for a given date to R2."""
    try:
        _client().put_object(
            Bucket=os.getenv("BUCKET_NAME"),
            Key=_key(target_date),
            Body=json.dumps(history, indent=2),
            ContentType="application/json",
        )
    except Exception:
        pass  # Fail silently — chat history loss is not critical


def clear_chat(target_date: date):
    """Delete chat history for a given date."""
    try:
        _client().delete_object(Bucket=os.getenv("BUCKET_NAME"), Key=_key(target_date))
    except Exception:
        pass
