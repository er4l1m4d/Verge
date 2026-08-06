"""Verify all four env vars load via python-dotenv — Step 0.4."""
import os
from dotenv import load_dotenv

load_dotenv()

required = ["SUPABASE_URL", "SUPABASE_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

all_ok = True
for var in required:
    val = os.environ.get(var)
    if val is None:
        print(f"FAIL: {var} not set")
        all_ok = False
    elif val == "":
        print(f"OK:   {var} = (empty — will be filled in Phase 6)")
    else:
        # Mask sensitive values in output
        display = val[:12] + "..." if len(val) > 12 else val
        print(f"OK:   {var} = {display}")

if all_ok:
    print("\nAll four variables loadable — no hardcoded secrets.")
else:
    print("\nSome variables missing — check backend/.env")
    exit(1)
