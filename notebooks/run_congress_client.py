#%%
import os
from pprint import pprint

from dotenv import load_dotenv
from tqdm import tqdm

from src.congressapi_client import CongressAPIClient

load_dotenv()

CONGRESS_API_KEY = os.getenv("CONGRESS_API_KEY")

#%%

client = CongressAPIClient(
    api_key=CONGRESS_API_KEY,
    timeout=60,
    min_interval=0.1,   # set e.g. 0.1 to cap at ~10 rps
    max_tries=8,          # retry attempts for 429/5xx/timeouts
    backoff_base=0.75,  # base backoff seconds
    backoff_cap=30.0,    # max backoff sleep,
    sleep_minutes=15,     # sleep time when rate limit exhausted
)

#%%

# Example: get members from the 100th congress
members = client.get_members(congress=100)

mbr = client.get_member(members[0].bioguide_id)
pprint(mbr)

#%%

# Example: get bills from the 100th congress
bills = client.get_bills(100, 'hr', hydrate=False, limit=5)

#%%

# Example: get summaries for the bills retrieved above
summaries = []
for bill in tqdm(bills, desc="Getting bill summaries"):
    most_recent_summary = client.get_bill_summaries(congress=bill.congress,
                                        bill_type=bill.bill_type,
                                        bill_number=bill.bill_number)[-1]
    summaries.append(most_recent_summary)

summaries

#%%

# Example: get bill and amendment actions for the bills retrieved above

actions_params = {
    "congress": bills[1].congress,
    "bill_type": bills[1].bill_type,
    "bill_number": bills[1].bill_number
}

bill_actions = client.get_bill_actions(**actions_params)

print("Bill Actions for :", actions_params)
print(bill_actions)


amdt_actions = []
for b in bills:
    if b.amendments:
        amendment_params = {
            "congress": bills[1].amendments[0].congress,
            "amendment_type": bills[1].amendments[0].amendment_type,
            "amendment_number": int(bills[1].amendments[0].amendment_number)
        }
        actions = client.get_amendment_actions(**amendment_params)
        amdt_actions.append(actions)

print("\nAmendment Actions:")
print(amdt_actions)

#%%

# Example: get bill subjects
subject_params = {
    "congress": bills[3].congress,
    "bill_type": bills[3].bill_type,
    "bill_number": bills[3].bill_number
}

bill_subjects = client.get_bill_subjects(**subject_params)
bill_subjects

#%%

# Example: pull hearing data for house and senate armed services and foreign affairs/international relations committees
TARGETS = {"hsas00", "ssas00", "ssfr00", "hsfa00"}

all_hearings = client.get_hearings(congress=118, chamber="house")

# %%

hearings_to_keep=[]
seen_jackets = set()  # dedupe: one entry per hearing, not one per format
for i, h in enumerate(tqdm(all_hearings)):
    if h.jacket_number in seen_jackets:
        continue
    full = client.get_hearing(congress=h.congress,
                              chamber=h.chamber.lower(),
                              jacket_number=h.jacket_number)
    if any(c["systemCode"] in TARGETS for c in full.committees):
        urls = [f.url for f in full.formats if f.type in ("PDF", "Formatted Text")]
        if urls:
            seen_jackets.add(h.jacket_number)
            hearings_to_keep.append({
                "jacket_number": h.jacket_number,
                "title": full.title,
                "urls": urls,
                "committee": full.committees
            })
            print(full.title, urls)
    if len(hearings_to_keep) >= 10:
        break
# %%
