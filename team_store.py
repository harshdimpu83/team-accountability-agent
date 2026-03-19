import boto3
import json
import os
from botocore.config import Config

R2_KEY = "team-accountability/team.json"


def _client():
    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{os.getenv('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(s3={"addressing_style": "path"}),
    )


def load_team() -> list:
    try:
        response = _client().get_object(Bucket=os.getenv("BUCKET_NAME"), Key=R2_KEY)
        return json.loads(response["Body"].read().decode("utf-8"))
    except _client().exceptions.NoSuchKey:
        return []
    except Exception:
        return []


def save_team(team: list):
    _client().put_object(
        Bucket=os.getenv("BUCKET_NAME"),
        Key=R2_KEY,
        Body=json.dumps(team, indent=2),
        ContentType="application/json",
    )


def add_member(name: str, email: str):
    team = load_team()
    team.append({"name": name, "email": email})
    save_team(team)


def update_member(index: int, name: str, email: str):
    team = load_team()
    team[index] = {"name": name, "email": email}
    save_team(team)


def delete_member(index: int):
    team = load_team()
    team.pop(index)
    save_team(team)
