from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class Subcommittee:
    system_code: Optional[str]
    name: Optional[str]
    raw: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Committee:
    system_code: Optional[str]
    name: Optional[str]
    chamber: Optional[str]                    # present on list payloads
    committee_type: Optional[str]             # committeeTypeCode on list payloads
    parent_system_code: Optional[str]
    parent_name: Optional[str]
    subcommittees: List[Subcommittee] = field(default_factory=list)
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CommitteeMeeting:
    event_id: Optional[int]
    type: Optional[str]
    title: Optional[str]
    meeting_status: Optional[str]
    date: Optional[str]
    chamber: Optional[str]
    congress: Optional[int] = None
    # Core committees array, as seen in list+detail
    committees: List[Dict[str, Any]] = field(default_factory=list)

    # Detail-only enrichments (map them when present):
    hearing_transcript: Optional[dict] = None
    location: Optional[str] = None            # sometimes a separate field
    room: Optional[str] = None                # sometimes present
    witnesses: List[Dict[str, Any]] = field(default_factory=list)
    documents: List[Dict[str, Any]] = field(default_factory=list)   # meetingDocuments or similar
    videos: List[Dict[str, Any]] = field(default_factory=list)      # video links/ids
    related_bills: List[Dict[str, Any]] = field(default_factory=list)
    related_nominations: List[Dict[str, Any]] = field(default_factory=list)
    related_treaties: List[Dict[str, Any]] = field(default_factory=list)

    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

@dataclass
class HearingFormat:
    type: str
    url: str


@dataclass
class Hearing:
    jacket_number: Union[int, str]  # Can be either integer or string
    title: Optional[str] = None
    congress: Optional[int] = None
    chamber: Optional[str] = None
    citation: Optional[str] = None
    committees: List[Dict[str, str]] = field(default_factory=list)  # {"name","systemCode"}
    dates: List[str] = field(default_factory=list)                  # ISO dates
    formats: List[HearingFormat] = field(default_factory=list)      # PDF/Formatted Text
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemberRole:
    congress: Optional[int] = None
    chamber: Optional[str] = None
    title: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Member:
    bioguide_id: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None  # Middle name/initial when available
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    party: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None  # Congressional district (for House members)
    chamber: Optional[str] = None
    is_current: Optional[bool] = None
    roles: List[MemberRole] = field(default_factory=list)

    # Sponsorship metadata (when Member represents a sponsor/cosponsor)
    sponsorship_date: Optional[str] = None  # Date they became a sponsor/cosponsor
    sponsorship_withdrawn_date: Optional[str] = None  # Date they withdrew (if applicable)
    is_original_cosponsor: Optional[bool] = None  # True if original cosponsor, False if added later

    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BillTextVersion:
    type: Optional[str] = None
    url: Optional[str] = None
    date: Optional[str] = None
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Amendment:
    congress: int
    amendment_type: str  # "HAMDT", "SAMDT", etc.
    amendment_number: int
    description: Optional[str] = None
    purpose: Optional[str] = None
    latest_action: Optional[str] = None
    latest_action_date: Optional[str] = None

    # Amendment metadata
    chamber: Optional[str] = None  # Chamber where amendment was proposed
    proposed_date: Optional[str] = None  # Date amendment was proposed
    submitted_date: Optional[str] = None  # Date amendment was submitted

    # Information about the bill being amended
    amended_bill: Optional[Dict[str, Any]] = None  # Bill that this amendment modifies

    # Sponsorship information for amendments
    sponsors: List[Member] = field(default_factory=list)
    cosponsors: List[Member] = field(default_factory=list)
    cosponsors_count: Optional[int] = None
    cosponsors_count_including_withdrawn: Optional[int] = None
    cosponsors_url: Optional[str] = None

    # Related content URLs
    actions_url: Optional[str] = None
    actions_count: Optional[int] = None
    amendments_url: Optional[str] = None  # Amendments to this amendment
    amendments_count: Optional[int] = None
    text_url: Optional[str] = None  # Amendment text
    text_count: Optional[int] = None  # Number of text versions

    # Metadata
    update_date: Optional[str] = None
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Bill:
    congress: int
    bill_type: str
    bill_number: int
    title: Optional[str] = None
    introduced_date: Optional[str] = None  # ISO date when bill was introduced
    origin_chamber: Optional[str] = None  # "House" or "Senate"
    origin_chamber_code: Optional[str] = None  # "H" or "S"
    latest_action: Optional[str] = None
    latest_action_date: Optional[str] = None  # Date of latest action

    # Primary sponsor information
    sponsors: List[Member] = field(default_factory=list)  # Consolidated sponsor list

    # Policy and subject information
    policy_area: Optional[Dict[str, str]] = None  # {"name": "Policy Area Name"}

    # Legislative status
    laws: List[Dict[str, Any]] = field(default_factory=list)  # If bill became law

    # Legislative metadata
    constitutional_authority_statement: Optional[str] = None  # Constitutional authority statement text
    cbo_cost_estimates: List[Dict[str, Any]] = field(default_factory=list)  # CBO cost estimates
    committee_reports: List[Dict[str, Any]] = field(default_factory=list)  # Committee reports

    # Cosponsorship information
    cosponsors_count: Optional[int] = None
    cosponsors_count_including_withdrawn: Optional[int] = None
    cosponsors: List[Member] = field(default_factory=list)  # Full list if fetched
    cosponsors_url: Optional[str] = None  # API URL to fetch full cosponsors list

    # Related content URLs (for optional hydration)
    actions_url: Optional[str] = None  # URL to fetch bill actions
    actions_count: Optional[int] = None
    amendments_url: Optional[str] = None  # URL to fetch bill amendments
    amendments_count: Optional[int] = None
    committees_url: Optional[str] = None  # URL to fetch associated committees
    committees_count: Optional[int] = None
    related_bills_url: Optional[str] = None  # URL to fetch related bills
    related_bills_count: Optional[int] = None
    subjects_url: Optional[str] = None  # URL to fetch legislative subjects
    subjects_count: Optional[int] = None
    summaries_url: Optional[str] = None  # URL to fetch bill summaries
    summaries_count: Optional[int] = None
    titles_url: Optional[str] = None  # URL to fetch all bill titles
    titles_count: Optional[int] = None

    # Amendment information
    amendments: List[Amendment] = field(default_factory=list)  # Full list if fetched via hydration

    # External URLs
    legislation_url: Optional[str] = None  # Congress.gov public URL
    urls: List[str] = field(default_factory=list)  # Other URLs
    texts: List[BillTextVersion] = field(default_factory=list)

    # Metadata
    update_date: Optional[str] = None
    update_date_including_text: Optional[str] = None
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
