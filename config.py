"""Central configuration, loaded once from the .env file.

Everything the rest of the code needs to know about *you* (your FTP, your
weight, which model to use) lives here, so no other module has to reach into
environment variables directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root into os.environ.
load_dotenv()

# --- Paths -----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
TRAINING_PLAN_PATH = DATA_DIR / "training_plan.json"
RECENT_ACTIVITIES_PATH = DATA_DIR / "recent_activities.json"
# Ledger of proposed/approved plan adjustments (propose->approve). Git-ignored:
# it references the personal plan. The base plan file itself is never mutated.
PLAN_ADJUSTMENTS_PATH = DATA_DIR / "plan_adjustments.json"

# --- Athlete profile -------------------------------------------------------
# FTP is the source of truth for all power-based load math (NP, IF, power TSS).
FTP_WATTS = int(os.getenv("FTP_WATTS", "253"))
WEIGHT_LB = float(os.getenv("WEIGHT_LB", "175")) or None

# Heart-rate anchors for the cross-sport hr_tss load estimate (any sport).
RESTING_HR = int(os.getenv("RESTING_HR", "39"))
THRESHOLD_HR = int(os.getenv("THRESHOLD_HR", "163"))

# --- LLM provider ----------------------------------------------------------
# Which LLM backs the analysis. Only "anthropic" is implemented today; the
# provider seam (llm.py) means adding another is a new adapter, not surgery.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
# Model for the Anthropic adapter. To trade quality for cost, set this to
# claude-sonnet-5 in your .env.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
# The eval judge's model — held FIXED so model-comparison scores stay comparable
# (varying the judge too would move two variables at once). See evaluate.py.
EVAL_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-sonnet-5")

# --- Strava API (Phase 2) --------------------------------------------------
# From your Strava API application at https://www.strava.com/settings/api.
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
# Where OAuth tokens are cached (git-ignored). Created by strava_auth.py.
STRAVA_TOKENS_PATH = PROJECT_ROOT / ".strava_tokens.json"
# Strava activity IDs already analyzed, so sync.py skips them (git-ignored).
SYNCED_IDS_PATH = PROJECT_ROOT / ".synced_ids.json"
# SQLite run log + eval results (git-ignored observability data).
RUNS_DB_PATH = PROJECT_ROOT / "runs.db"

# --- Notifications (Phase 3) -----------------------------------------------
# Active delivery channels, comma-separated (e.g. "email"). Empty = no delivery,
# so nothing is ever sent until you opt in via .env.
NOTIFY_CHANNELS = [c.strip() for c in os.getenv("NOTIFY_CHANNELS", "").split(",") if c.strip()]
# Gmail delivery: your address + a 16-char App Password (NOT your login password).
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
# Where analyses are sent (defaults to your own Gmail address).
NOTIFY_TO = os.getenv("NOTIFY_TO") or GMAIL_ADDRESS

# --- Discord approver bot (interactive plan-adjustment approval) ------------
# A persistent bot (discord_bot.py) sends pending plan adjustments with
# Approve/Reject buttons, so you approve from your phone instead of SSH.
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
# YOUR Discord user id (right-click your name -> Copy ID). The bot DMs proposals
# here by default, and only this user may approve/reject (the auth gate).
DISCORD_APPROVER_ID = int(os.getenv("DISCORD_APPROVER_ID", "0")) or None
# Optional: set a channel id to post proposals in a channel INSTEAD of DMing you.
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0")) or None

# Output units for the whole app. You chose imperial.
UNITS = "imperial"
