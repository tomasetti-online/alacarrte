import os
import tempfile

# Point ALACarrte at a scratch dir BEFORE the app module is imported.
# app.py calls os.makedirs(DATA_DIR) at import time.
os.environ["ALACARTTE_DATA"] = tempfile.mkdtemp(prefix="alacarrte-test-")
# Small rate limit so the rate-limit test doesn't need 60 calls.
os.environ["ALACARTTE_RATE_LIMIT"] = "3"
os.environ["ALACARTTE_CONCURRENCY"] = "2"
# Keep notifications off in tests.
os.environ["ALACARTTE_GOTIFY_URL"] = ""
os.environ["ALACARTTE_HA_WEBHOOK"] = ""
