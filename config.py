"""ALACarrte configuration.

Single owner of every tunable. Reads all settings from environment variables
with sensible defaults; other modules import from here instead of re-reading
os.environ. No logic lives here beyond turning env strings into typed values.
"""
import os

DATA_DIR = os.environ.get("ALACARTTE_DATA", "/data")
os.makedirs(DATA_DIR, exist_ok=True)

GOTIFY_URL = os.environ.get("ALACARTTE_GOTIFY_URL", "")
GOTIFY_TOKEN = os.environ.get("ALACARTTE_GOTIFY_TOKEN", "")
PLEX_DIR = os.environ.get("ALACARTTE_PLEX_DIR", "")
HA_WEBHOOK = os.environ.get("ALACARTTE_HA_WEBHOOK", "")
ADMIN_IPS = set(os.environ.get("ALACARTTE_ADMIN_IPS", "").replace(" ", "").split(",")) - {""}
CONCURRENCY = int(os.environ.get("ALACARTTE_CONCURRENCY", "3"))
SLEEP_REQUESTS = os.environ.get("ALACARTTE_SLEEP_REQUESTS", "3.0")
RATE_LIMIT = int(os.environ.get("ALACARTTE_RATE_LIMIT", "60"))
DL_COOLDOWN = int(os.environ.get("ALACARTTE_DL_COOLDOWN", "1"))
AD_SCRIPT = os.environ.get("ALACARTTE_AD_SCRIPT", "")
VPN_PROXY = os.environ.get("ALACARTTE_VPN_PROXY", "")  # e.g. http://vpn-host:18888
VPN_CONTROL = os.environ.get("ALACARTTE_VPN_CONTROL", "")  # script path or API URL

# Download library cap: how many completed jobs to keep indexed.
LIBRARY_CAP = 50

# Age (seconds) after which finished, non-library jobs are purged.
CLEANUP_INTERVAL = 600

SECRET_KEY = os.environ.get("ALACARTTE_SECRET_KEY", os.urandom(24).hex())