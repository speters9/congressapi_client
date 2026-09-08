# congressapi-client

Typed Python wrapper around the Library of Congress **Congress.gov v3 API**, covering members, bills, amendments, committees, hearings, committee meetings, votes, and bill/amendment actions.

Full API reference (all parameters and data model fields) is available in the documentation [here](https://speters9.github.io/congressapi_client/).

## Features

- **Typed data models** - dataclasses for all major entities, each with a `raw` field holding the full API response
- **Sponsorship networks** - sponsor/cosponsor tracking for bills and amendments, with optional `hydrate=True` for full detail
- **Rate limiting** - token bucket limiter that respects the API's 5000 requests/hour cap
- **Retry/backoff** - exponential backoff with jitter, honors `Retry-After` headers on 429/5xx responses
- **Streaming** - `iter_entities()` for large-scale, filtered data collection across one or more congresses
- **JSON & XML** - transparently handles both response formats from the API

## Install

```bash
pip install git+https://github.com/speters9/congressapi-client.git@main
# or pin a tag:
pip install git+https://github.com/speters9/congressapi-client.git@v0.1.0
```

Get an API key at [api.congress.gov](https://api.congress.gov/sign-up/), then save it in a `.env` file in your project root:

```bash
# .env
CONGRESS_API_KEY=your-key-here
```

## Quick Start

```python
from dotenv import load_dotenv
from congressapi_client import CongressAPIClient

load_dotenv()  # reads CONGRESS_API_KEY from .env
client = CongressAPIClient()  # or pass api_key="..." directly

# Get a specific bill
bill = client.get_bill(117, "hr", 3076)
print(f"{bill.title}")
print(f"Cosponsors: {bill.cosponsors_count}")

# Get with full cosponsor list (slower - extra API call)
bill = client.get_bill(117, "hr", 3076, hydrate=True)
for cosponsor in bill.cosponsors:
    print(f"  {cosponsor.full_name} - {cosponsor.sponsorship_date}")

# Get a specific member
member = client.get_member("Y000064")  # Todd Young
print(f"{member.full_name} ({member.party}-{member.state})")
for term in member.terms:
    print(f"  Congress {term.congress}: {term.chamber} ({term.start_year}-{term.end_year or 'present'})")

# Get roll call votes (BETA endpoint - may have incomplete data)
votes = client.get_votes(chamber="house", congress=118, session=1, limit=10)
for vote in votes:
    print(f"Vote #{vote.vote_number}: {vote.vote_result}")
```

## Other Functionality

The client exposes list + detail methods for every entity type, following the same pattern as `get_bill`/`get_bills`:

| Entity             | List                                                       | Detail                                                                |
| ------------------ | ---------------------------------------------------------- | --------------------------------------------------------------------- |
| Members            | `get_members(congress, chamber, state, district, current)` | `get_member(bioguide_id)`                                             |
| Bills              | `get_bills(congress, bill_type, ...)`                      | `get_bill(congress, bill_type, bill_number, hydrate=)`                |
| Amendments         | `get_amendments(congress, amendment_type)`                 | `get_amendment(congress, amendment_type, amendment_number, hydrate=)` |
| Committees         | `get_committees(congress, chamber)`                        | `get_committee(chamber, system_code)`                                 |
| Hearings           | `get_hearings(congress, chamber)`                          | `get_hearing(congress, chamber, jacket_number)`                       |
| Committee Meetings | `get_committee_meetings(congress, chamber)`                | `get_committee_meeting(congress, chamber, event_id)`                  |
| Votes              | `get_votes(chamber, congress, session)`                    | `get_vote(chamber, congress, session, vote_number, include_members=)` |

Related helper methods: `get_bill_actions`, `get_amendment_actions`, `get_bill_cosponsors`, `get_bill_amendments`, `get_bill_subjects`, `get_bill_summaries`, `get_amendment_cosponsors`, `get_vote_members`.

For bulk/filtered collection across one or more congresses, use `iter_entities()`:

```python
for bill in client.iter_entities(
    entity="bill",
    congress_range=(117, 118),
    hydrate=True,
    where=lambda b: b.get("policyArea", {}).get("name") == "Healthcare",
    continue_on_error=True,
):
    print(f"{bill.title}: {len(bill.cosponsors)} cosponsors")
```

## How the Client Works

**Rate limiting:** a token bucket tracks requests against `req_per_hour` (default 5000, minus a small `rph_margin` safety buffer). When the bucket runs dry, the client sleeps for `sleep_minutes` (default 15) to let tokens accumulate rather than hammering the API.

**Retries/backoff:** failed requests (429/500/502/503/504, connection errors, timeouts) are retried up to `max_tries` times with full-jitter exponential backoff (`backoff_base` \* 2^attempt, capped at `backoff_cap`). A `Retry-After` header (seconds or HTTP-date) is honored when present.

**Pagination:** list endpoints are automatically paginated by following `pagination.next` links until exhausted or `limit` is reached.

**Hydration:** list methods return summary data by default (fast, one call per page). Pass `hydrate=True` to fetch full detail per item (sponsors, cosponsors, subjects, summaries, etc.) at the cost of one extra API call per item.

**Error handling:** bulk operations accept `continue_on_error` (default `True`) to log and skip failed items instead of raising.

Key constructor options:

```python
CongressAPIClient(
    api_key: str | None = None,      # defaults to CONGRESS_API_KEY / CONGRESS_DOT_GOV_API_KEY env var
    timeout: int = 60,
    min_interval: float = 0.1,       # politeness delay between requests (seconds)
    max_tries: int = 8,
    backoff_base: float = 0.75,
    backoff_cap: float = 60.0,
    limit: int = 250,                # results per page (API max 250)
    req_per_hour: int = 5000,
    rph_margin: float = 0.01,        # safety margin subtracted from req_per_hour
    sleep_minutes: int = 15,         # sleep time when the hourly budget is exhausted
)
```

## Object Types

Each entity returned by the client has a corresponding dataclass in `congressapi_client.models` (`Member`, `Bill`, `Amendment`, `BillAction`, `Vote`, `VoteMember`, `Committee`, `Hearing`, `CommitteeMeeting`, etc.). Every dataclass carries a `raw` field with the unmodified API response, so you can always fall back to fields not yet promoted to typed attributes. Convert any object to a dict for export with `dataclasses.asdict()`.

See the hosted docs [here](https://speters9.github.io/congressapi_client/) for the full field listing per type.

## Notes

- Bill/amendment type strings (e.g. `"hr"`, `"samdt"`) are automatically lowercased for API endpoints.
- The `*-vote` endpoints (`get_votes`, `get_vote`, `get_vote_members`) are BETA on Congress.gov and may be incomplete for some congresses/sessions.
- See [Congress.gov Action Codes](https://www.congress.gov/help/field-values/action-codes) for the meaning of `BillAction.action_code`.

## License

MIT

## Contributing

Issues and pull requests welcome!
