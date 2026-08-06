"""Quick connection test for Supabase — Step 0.3."""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("FAIL: SUPABASE_URL or SUPABASE_KEY not set in .env")
    exit(1)

client = create_client(url, key)

try:
    result = client.rpc("select 1").execute()
    print(f"OK: connected — {result}")
except Exception as e:
    # select 1 via RPC may not work without a function; try a raw query
    print(f"RPC failed ({e}), trying raw table list...")
    try:
        tables = client.table("_test_nonexistent").select("*").limit(0).execute()
        print(f"OK: connection works (table query returned: {tables})")
    except Exception as e2:
        # "relation/table does not exist" still proves auth + connection work
        err = str(e2).lower()
        if any(phrase in err for phrase in ["does not exist", "not found", "could not find the table", "42p01", "pgrst205"]):
            print("OK: connection works (relation not found is expected — no schema yet)")
        else:
            print(f"FAIL: {e2}")
            exit(1)
