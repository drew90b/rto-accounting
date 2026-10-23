import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/rto_accounting"
)

STORAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "storage",
    "receipts"
)

# --- Sign-in -----------------------------------------------------------------
# SESSION_SECRET signs the session cookie. It is required: app.main refuses to
# start without it. Changing it signs everyone out.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
# Comma-separated origins the app is served from, e.g. "http://100.123.116.111:8000".
# Every POST must carry an Origin (or Referer) that matches one of these.
ALLOWED_ORIGINS = [
    o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
# Set true only when the app is served over HTTPS (e.g. behind Tailscale Serve).
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
# Absolute session length, measured from sign-in.
SESSION_MAX_HOURS = int(os.environ.get("SESSION_MAX_HOURS", "12"))
# A session unused for this long ends, even inside the absolute limit.
SESSION_IDLE_MINUTES = int(os.environ.get("SESSION_IDLE_MINUTES", "30"))
