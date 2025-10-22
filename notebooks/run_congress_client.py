#%%
import os

from dotenv import load_dotenv
from tqdm import tqdm

from src.congressapi_client import CongressAPIClient

load_dotenv()

CONGRESS_API_KEY = os.getenv("CONGRESS_API_KEY")

#%%

client = CongressAPIClient(
    api_key=CONGRESS_API_KEY,
    timeout=60,
    min_interval=0.0,   # set e.g. 0.1 to cap at ~10 rps
    max_tries=8,          # retry attempts for 429/5xx/timeouts
    backoff_base=0.75,  # base backoff seconds
    backoff_cap=30.0,    # max backoff sleep,
    sleep_minutes=15,     # sleep time when rate limit exhausted
)

#%%

bills = client.get_bills(117, 'hr', hydrate=True, limit=5)

#%%
TARGETS = {"hsas00", "ssas00", "ssfr00", "hsfa00"}

all_hearings = client.get_hearings(congress=118, chamber="house")

# %%

hearings_to_keep=[]
for i, h in enumerate(tqdm(all_hearings)):
    full = client.get_hearing(congress=h.congress,
                              chamber=h.chamber.lower(),
                              jacket_number=h.jacket_number)
    if any(c["systemCode"] in TARGETS for c in full.committees):
        for f in full.formats:
            if f.type in ("PDF", "Formatted Text"):
                hearings_to_keep.append({
                    "title": full.title,
                    "url": f.url,
                    "committee": full.committees
                })
                print(full.title, f.url)
    if len(hearings_to_keep) >= 10:
        break
# %%
