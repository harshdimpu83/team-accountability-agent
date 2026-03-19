import json
import os

TEAM_FILE = os.path.join(os.path.dirname(__file__), "team.json")


def load_team() -> list:
    if not os.path.exists(TEAM_FILE):
        return []
    with open(TEAM_FILE, "r") as f:
        return json.load(f)


def save_team(team: list):
    with open(TEAM_FILE, "w") as f:
        json.dump(team, f, indent=2)


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
