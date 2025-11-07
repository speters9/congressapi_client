# congressapi-client

Typed wrapper around the Library of Congress **Congress.gov v3 API**, providing comprehensive access to:

- **Members of Congress** - Full biographical data, service history, party affiliations, and legislative activity
- **Legislation** - Bills and amendments with sponsorship networks and cosponsorship tracking
- **Committees** - Committees, subcommittees, and committee activities
- **Hearings** - Hearing transcripts, witnesses, and documentation
- **Committee Meetings** - Meeting schedules, documents, and participants

## Features

- **Rich Data Models** - Comprehensive dataclasses with all available API fields
- **Sponsorship Networks** - Full sponsor and cosponsor tracking for bills and amendments
- **Panel Data Ready** - Member service history structured for longitudinal analysis
- **Error Handling** - Optional continue-on-error for resilient bulk operations
- **Rate Limiting** - Token bucket rate limiting with automatic backoff (respects 5000 req/hour API limit)
- **Retry Logic** - Exponential backoff with jitter and `Retry-After` support
- **Flexible Queries** - Filter by congress, chamber, state, date ranges, and more

## Install (from GitHub)

```bash

pip install git+https://github.com/<you>/congressapi-client.git@main
# or pin a tag:
pip install git+https://github.com/<you>/congressapi-client.git@v0.1.0

```

## Usage

### Quick Start

```python
from congressapi_client import CongressAPIClient

# Initialize client (reads CONGRESS_API_KEY from environment)
client = CongressAPIClient()

# Get current House members from Colorado
members = client.get_members(congress=119, chamber="house", state="CO", current=True)
print(f"{members[0].full_name} - {members[0].party}")

# Get a specific bill with full details
bill = client.get_bill(117, "hr", 3076)
print(f"{bill.title}")
print(f"Sponsors: {len(bill.sponsors)}")
print(f"Cosponsors: {bill.cosponsors_count}")

# Get bill with full cosponsor list (slower, makes additional API calls)
bill_full = client.get_bill(117, "hr", 3076, hydrate=True)
for cosponsor in bill_full.cosponsors:
    print(f"  {cosponsor.full_name} - {cosponsor.sponsorship_date}")
```

### Members of Congress

**List Members:**

```python
# Get all current senators
senators = client.get_members(chamber="senate", current=True)

# Get House members from California in 118th Congress
ca_reps = client.get_members(congress=118, chamber="house", state="CA")

# Get all members (no filters)
all_members = client.get_members()
```

**Individual Member Details:**

```python
# Get comprehensive member information
member = client.get_member("Y000064")  # Todd Young

# Access biographical info
print(f"Name: {member.full_name}")
print(f"Born: {member.birth_year}")
print(f"Party: {member.party}")
print(f"State: {member.state}")

# Service history
for term in member.terms:
    print(f"Congress {term.congress}: {term.chamber} ({term.start_year}-{term.end_year or 'present'})")

# Party history (track party switches)
for affiliation in member.party_history:
    end = affiliation.end_year or "present"
    print(f"{affiliation.party_name}: {affiliation.start_year}-{end}")

# Leadership roles
for role in member.leadership_roles:
    print(f"{role.type} (Congress {role.congress})")

# Legislative activity
print(f"Sponsored: {member.sponsored_legislation_count} bills")
print(f"Cosponsored: {member.cosponsored_legislation_count} bills")

# Contact information
print(f"Office: {member.office_address}")
print(f"Phone: {member.phone_number}")
print(f"Website: {member.official_website_url}")
```

### Bills and Legislation

**List Bills:**

```python
# Get all House bills from 118th Congress
bills = client.get_bills(congress=118, bill_type="hr")

# Filter by introduction date
recent_bills = client.get_bills(
    congress=118,
    bill_type="s",
    introduced_start="2023-01-01",
    introduced_end="2023-12-31"
)

# Get bills with full details (slower - makes individual API calls)
bills_full = client.get_bills(
    congress=118,
    bill_type="hr",
    hydrate=True,  # Fetch full details including cosponsors
    limit=10  # Limit to 10 bills to avoid long wait times
)
```

**Individual Bill Details:**

```python
bill = client.get_bill(117, "hr", 3076)

# Basic info
print(f"Title: {bill.title}")
print(f"Introduced: {bill.introduced_date}")
print(f"Status: {bill.latest_action}")
print(f"Policy Area: {bill.policy_area}")

# Sponsors
for sponsor in bill.sponsors:
    print(f"Sponsor: {sponsor.full_name} ({sponsor.party}-{sponsor.state})")

# Cosponsorship summary (fast - no additional API calls)
print(f"Total cosponsors: {bill.cosponsors_count}")
print(f"Including withdrawn: {bill.cosponsors_count_including_withdrawn}")

# Get full cosponsor list (slower - makes additional API call)
bill_with_cosponsors = client.get_bill(117, "hr", 3076, hydrate=True)
for cosponsor in bill_with_cosponsors.cosponsors:
    status = "Original" if cosponsor.is_original_cosponsor else "Added"
    withdrawn = f" (withdrawn {cosponsor.sponsorship_withdrawn_date})" if cosponsor.sponsorship_withdrawn_date else ""
    print(f"{cosponsor.full_name} - {status} on {cosponsor.sponsorship_date}{withdrawn}")

# Related content (URLs for additional API calls)
print(f"Actions: {bill.actions_count} - {bill.actions_url}")
print(f"Amendments: {bill.amendments_count} - {bill.amendments_url}")
print(f"Committee Reports: {len(bill.committee_reports)}")
```

**Get Bill Cosponsors:**

```python
# Fetch full cosponsor list separately
cosponsors = client.get_bill_cosponsors(117, "hr", 3076)
for cosponsor in cosponsors:
    print(f"{cosponsor.full_name}: {cosponsor.sponsorship_date}")
```

### Amendments

**List Amendments:**

```python
# Get all Senate amendments from 118th Congress
amendments = client.get_amendments(congress=118, amendment_type="samdt")

# Get all amendments for a congress
all_amendments = client.get_amendments(congress=117)
```

**Individual Amendment Details:**

```python
amendment = client.get_amendment(118, "samdt", 1123)

print(f"Description: {amendment.description}")
print(f"Purpose: {amendment.purpose}")
print(f"Chamber: {amendment.chamber}")
print(f"Bill being amended: {amendment.amended_bill}")

# Sponsors and cosponsors (same as bills)
for sponsor in amendment.sponsors:
    print(f"Sponsor: {sponsor.full_name}")

# Get with full cosponsor list
amendment_full = client.get_amendment(118, "samdt", 1123, hydrate=True)
print(f"Cosponsors: {len(amendment_full.cosponsors)}")
```

**Get Bill Amendments:**

```python
# Get amendments to a specific bill
amendments = client.get_bill_amendments(118, "hr", 815)

# Get amendments with full sponsor/cosponsor details (slower)
amendments_full = client.get_bill_amendments(118, "hr", 815, hydrate=True)
for amdt in amendments_full:
    print(f"Amendment {amdt.amendment_number}: {len(amdt.cosponsors)} cosponsors")
```

### Committees

**List Committees:**

```python
# Get all House committees
committees = client.get_committees(chamber="house")

# Get Senate committees for specific congress
senate_comms = client.get_committees(congress=118, chamber="senate")

for comm in committees:
    print(f"{comm.name} ({comm.system_code})")
    for sub in comm.subcommittees:
        print(f"  - {sub.name}")
```

**Individual Committee Details:**

```python
committee = client.get_committee("house", "hswm00")  # Ways and Means
print(f"Name: {committee.name}")
print(f"Subcommittees: {len(committee.subcommittees)}")
```

### Hearings

**List Hearings:**

```python
# Get all House hearings from 116th Congress
hearings = client.get_hearings(congress=116, chamber="house")

for hearing in hearings:
    print(f"{hearing.title}")
    for format in hearing.formats:
        print(f"  {format.type}: {format.url}")
```

**Individual Hearing Details:**

```python
hearing = client.get_hearing(116, "senate", 37721)
print(f"Title: {hearing.title}")
print(f"Citation: {hearing.citation}")
print(f"Committees: {[c['name'] for c in hearing.committees]}")

# Download transcript
for format in hearing.formats:
    if format.type == "PDF":
        print(f"PDF: {format.url}")
```

### Committee Meetings

**List Meetings:**

```python
# Get all House committee meetings from 118th Congress
meetings = client.get_committee_meetings(congress=118, chamber="house")

for mtg in meetings:
    print(f"{mtg.title} - {mtg.date}")
    print(f"  Status: {mtg.meeting_status}")
```

**Individual Meeting Details:**

```python
meeting = client.get_committee_meeting(118, "house", 115281)
print(f"Title: {meeting.title}")
print(f"Location: {meeting.location}")
print(f"Witnesses: {len(meeting.witnesses)}")
print(f"Documents: {len(meeting.documents)}")
print(f"Related Bills: {len(meeting.related_bills)}")
```

### Advanced: Streaming with Filters

For large-scale data collection, use `iter_entities()` to stream results with optional filtering:

```python
# Stream all bills from 117th Congress with filtering
for bill in client.iter_entities(
    entity="bill",
    congress=117,
    hydrate=True,  # Get full details for each bill
    where=lambda b: b.get("policyArea", {}).get("name") == "Healthcare"  # Filter predicate
):
    print(f"{bill.title}: {len(bill.cosponsors)} cosponsors")

# Stream members with error handling
for member in client.iter_entities(
    entity="member",
    congress=118,
    chamber="house",
    hydrate=True,
    continue_on_error=True  # Skip failed requests and continue
):
    print(f"{member.full_name}: {len(member.terms)} terms")

# Stream across multiple congresses
for hearing in client.iter_entities(
    entity="hearing",
    congress_range=(115, 118),  # 115th through 118th Congress
    chamber="senate",
    hydrate=False  # Just get list summaries (faster)
):
    print(hearing.title)
```

### Error Handling

By default, bulk operations continue on errors. You can control this behavior:

```python
# Continue processing even if some requests fail (default)
bills = client.get_bills(
    congress=118,
    hydrate=True,
    continue_on_error=True  # Log errors but keep going
)

# Stop on first error
try:
    bills = client.get_bills(
        congress=118,
        hydrate=True,
        continue_on_error=False  # Raise exception on error
    )
except Exception as e:
    print(f"Failed: {e}")

# Streaming with error handling
for bill in client.iter_entities(
    entity="bill",
    congress=118,
    hydrate=True,
    continue_on_error=True  # Skip failed bills, continue processing
):
    # Process successful bills
    pass
```

## Configuration

**API Key Setup:**

```bash
export CONGRESS_API_KEY="your-key-here"
# or
export CONGRESS_DOT_GOV_API_KEY="your-key-here"
```

**Client Options:**

```python
CongressAPIClient(
    api_key: str | None = None,           # API key (defaults to env var)
    base_url: str = "https://api.congress.gov/v3",
    timeout: int = 60,                    # Request timeout in seconds
    min_interval: float = 0.0,            # Min seconds between requests (politeness throttle)
    max_tries: int = 8,                   # Max retry attempts for 429/5xx errors
    backoff_base: float = 0.75,           # Base backoff time in seconds
    backoff_cap: float = 60.0,            # Max backoff time in seconds
    limit: int = 250,                     # Results per page (max 250)
    log_level: int = logging.INFO,        # Logging level
    req_per_hour: int = 5000,             # Rate limit (API max is 5000)
    rph_margin: float = 0.01,             # Safety margin for rate limit (1%)
    sleep_minutes: int = 15,              # Minutes to sleep when rate limit exhausted
)
```

**Rate Limiting:**
The client uses token bucket rate limiting to respect the API's 5000 requests/hour limit:

- Automatically tracks request rate
- When bucket is empty, sleeps for `sleep_minutes` (default 15) to accumulate tokens
- Honors `Retry-After` headers from 429 responses
- Set `req_per_hour=0` to disable rate limiting for testing

## Data Models

### Member

Comprehensive congressional member information:

**Core Fields:**

- `bioguide_id`: Unique identifier
- `first_name`, `last_name`, `middle_name`, `full_name`: Name components
- `honorific_name`: Title (Mr., Ms., Dr., etc.)
- `birth_year`: Year of birth

**Current Status:**

- `state`, `district`: Current representation
- `party`: Current party affiliation
- `is_current`: Currently serving

**Service History:**

- `terms`: List of `MemberTerm` objects (congress, chamber, years, state, district)
- `party_history`: List of `PartyAffiliation` objects (party changes over time)
- `leadership_roles`: List of `LeadershipRole` objects

**Legislative Activity:**

- `sponsored_legislation_count`, `sponsored_legislation_url`
- `cosponsored_legislation_count`, `cosponsored_legislation_url`

**Contact Information:**

- `official_website_url`, `office_address`, `phone_number`
- `image_url`, `image_attribution`

**When Used as Sponsor/Cosponsor:**

- `sponsorship_date`: Date became sponsor/cosponsor
- `sponsorship_withdrawn_date`: Date withdrew (if applicable)
- `is_original_cosponsor`: Original vs. added later

### Bill

Legislative bill information:

**Core Fields:**

- `congress`, `bill_type`, `bill_number`, `title`
- `introduced_date`, `latest_action`, `latest_action_date`
- `origin_chamber`, `origin_chamber_code`
- `policy_area`: Policy area name string

**Sponsorship:**

- `sponsors`: List of `Member` objects (consolidated from API's sponsor/sponsors fields)
- `cosponsors`: List of `Member` objects (when hydrated)
- `cosponsors_count`, `cosponsors_count_including_withdrawn`
- `cosponsors_url`: URL to fetch full cosponsor list

**Legislative Status:**

- `laws`: List of public laws (if bill became law)
- `committee_reports`, `cbo_cost_estimates`
- `constitutional_authority_statement`

**Related Content (counts and URLs):**

- `actions_count`, `actions_url`
- `amendments_count`, `amendments_url`
- `committees_count`, `committees_url`
- `related_bills_count`, `related_bills_url`
- `subjects_count`, `subjects_url`
- `summaries_count`, `summaries_url`
- `titles_count`, `titles_url`

**Text Versions:**

- `texts`: List of `BillTextVersion` objects (PDF/text links)

### Amendment

Amendment information (similar structure to Bill):

**Core Fields:**

- `congress`, `amendment_type`, `amendment_number`
- `description`, `purpose`
- `chamber`, `proposed_date`, `submitted_date`
- `amended_bill`: Information about bill being amended

**Sponsorship:**

- `sponsors`, `cosponsors`: Lists of `Member` objects
- `cosponsors_count`, `cosponsors_count_including_withdrawn`

**Related Content:**

- `actions_count`, `actions_url`
- `amendments_count`, `amendments_url` (amendments to this amendment)
- `text_count`, `text_url`

### Committee

Committee information:

- `system_code`, `name`, `chamber`, `committee_type`
- `parent_system_code`, `parent_name` (for subcommittees)
- `subcommittees`: List of `Subcommittee` objects

### Hearing

Hearing information:

- `jacket_number`, `title`, `congress`, `chamber`, `citation`
- `committees`: List of committee info dicts
- `dates`: List of hearing dates
- `formats`: List of `HearingFormat` objects (PDF, Formatted Text, etc.)

### CommitteeMeeting

Committee meeting information:

- `event_id`, `type`, `title`, `meeting_status`, `date`, `chamber`
- `committees`: List of committee info dicts
- `witnesses`, `documents`, `videos`: Lists of related items (detail view only)
- `related_bills`, `related_nominations`, `related_treaties`

## Working with Panel Data

The `Member` model is designed for panel data construction:

```python
import pandas as pd

# Get members
members = client.get_members(congress=118)

# Build member-congress panel
panel_data = []
for member in members:
    for term in member.terms:
        # Find party for this congress
        party = None
        for p in member.party_history:
            if p.start_year <= term.start_year and (p.end_year is None or p.end_year >= term.start_year):
                party = p.party_abbreviation
                break

        panel_data.append({
            'bioguide_id': member.bioguide_id,
            'full_name': member.full_name,
            'congress': term.congress,
            'chamber': term.chamber,
            'state': term.state_code,
            'district': term.district,
            'party': party,
            'start_year': term.start_year,
            'end_year': term.end_year,
        })

df = pd.DataFrame(panel_data)
```

## Database Export

All dataclasses can be converted to dictionaries using `dataclasses.asdict()`:

```python
from dataclasses import asdict

# Convert to dictionary
member_dict = asdict(member)

# For nested structures, you may want to flatten or store in separate tables
# Example: Store member base info and terms separately

# Base member table
member_record = {
    'bioguide_id': member.bioguide_id,
    'full_name': member.full_name,
    'birth_year': member.birth_year,
    'current_party': member.party,
    'sponsored_count': member.sponsored_legislation_count,
}

# Terms table (one-to-many)
terms_records = [
    {
        'bioguide_id': member.bioguide_id,
        'congress': term.congress,
        'chamber': term.chamber,
        'start_year': term.start_year,
        'end_year': term.end_year,
    }
    for term in member.terms
]
```

## Performance Tips

- **Use filters**: Apply congress, chamber, state, date filters to reduce API calls
- **Avoid unnecessary hydration**: Only use `hydrate=True` when you need full details
- **Use limits**: Set `limit` parameter to restrict result sets during testing
- **Batch processing**: Use `iter_entities()` for streaming large datasets
- **Error tolerance**: Set `continue_on_error=True` for bulk operations
- **Rate limiting**: The client respects API limits automatically, but for large jobs, expect pauses

**Hydration Performance:**

- Non-hydrated list queries: ~1 API call per 250 results (fast)
- Hydrated queries: 1 API call per item (slow but complete)
- Example: `get_bills(hydrate=True, limit=100)` makes ~100+ API calls

## Notes

- List endpoints are automatically paginated; the client follows `pagination.next` links
- `Retry-After` headers (seconds or HTTP-date) are honored for 429/503 responses
- Case normalization: Bill/amendment types are automatically lowercased for API endpoints
- Raw data: All dataclasses include a `raw` field with the complete API response
- The client handles both JSON and XML responses from the API

## License

MIT

## Contributing

Issues and pull requests welcome!
