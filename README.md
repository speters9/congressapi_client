# congressapi-client

Typed wrapper around the Library of Congress \*\*Congress.gov v3 API\*\*, covering:

- Committees \& subcommittees
- Hearings (with transcript links)
- Committee meetings
- Members of Congress
- Legislation (bills)

Includes:

- Exponential backoff with jitter
- `Retry-After` support
- Simple rate-limit guard (min interval between requests)
- Dataclasses for ergonomic access


## Install (from GitHub)

```bash

pip install git+https://github.com/<you>/congressapi-client.git@main
# or pin a tag:
pip install git+https://github.com/<you>/congressapi-client.git@v0.1.0

```


## Usage
```python
from congressapi_client import CongressAPIClient
api = CongressAPIClient()  # make sure to include your CONGRESS_API_KEY

# Members
members = api.get_members(congress=119, chamber="house", state="CO", current=True)
print(members[0].full_name)

# Bills
bills = api.get_bills(congress=119, bill_type="hr")
bill = api.get_bill(117, "hr", 4346)

# Committees
comms = api.get_committees(congress=118, chamber="house")
ti = api.get_committee("house", "hspw00")  # Transportation & Infrastructure

# Hearings (transcript links in Hearing.formats)
hearings = api.get_hearings(congress=116, chamber="senate")
one = api.get_hearing(116, "senate", 37721)
for f in one.formats:
    print(f.type, f.url)

# Committee meetings
mtgs = api.get_committee_meetings(congress=118, chamber="house")
```

## Configuration

Set your API key:
```bash
export CONGRESS_API_KEY="your-key-here"
```

Constructor options:
```python
CongressAPIClient(
    api_key: str | None = None,
    base_url: str = "https://api.congress.gov/v3",
    timeout: int = 60,
    min_interval: float = 0.0,   # set e.g. 0.1 to cap at ~10 rps
    max_tries: int = 8,          # retry attempts for 429/5xx/timeouts
    backoff_base: float = 0.75,  # base backoff seconds
    backoff_cap: float = 30.0    # max backoff sleep
    limit: int = 250             # max returns for each page in paginated responses
    log_level: int = logging.INFO
)
```

## Notes
- List endpoints are paginated; the client auto-follows pagination.next links.
- Retry-After (seconds or HTTP-date) is honored for 429/503.
- For bulk pulls, consider setting min_interval (e.g., 0.1) to be a polite client.
