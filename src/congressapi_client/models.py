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
class MemberTerm:
    """Represents a term of service in Congress."""
    congress: Optional[int] = None
    chamber: Optional[str] = None  # "House of Representatives" or "Senate"
    member_type: Optional[str] = None  # "Representative" or "Senator"
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    state_code: Optional[str] = None  # Two-letter state code
    state_name: Optional[str] = None  # Full state name
    district: Optional[int] = None  # District number (for House members)


@dataclass
class PartyAffiliation:
    """Represents party affiliation history."""
    party_name: Optional[str] = None  # Full party name
    party_abbreviation: Optional[str] = None  # Party abbreviation (R, D, I, etc.)
    start_year: Optional[int] = None
    end_year: Optional[int] = None  # None if current


@dataclass
class LeadershipRole:
    """Represents a leadership position held."""
    congress: Optional[int] = None
    type: Optional[str] = None  # Type of leadership role
    current: Optional[bool] = None


@dataclass
class Member:
    bioguide_id: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None  # invertedOrderName or directOrderName
    honorific_name: Optional[str] = None  # Mr., Ms., Dr., etc.

    # Current status
    state: Optional[str] = None
    district: Optional[int] = None  # Current district (for House members)
    party: Optional[str] = None  # Current party
    is_current: Optional[bool] = None  # Currently serving member

    # Biographical information
    birth_year: Optional[str] = None

    # Historical data
    terms: List[MemberTerm] = field(default_factory=list)  # Terms of service
    party_history: List[PartyAffiliation] = field(default_factory=list)  # Party affiliation history
    leadership_roles: List[LeadershipRole] = field(default_factory=list)  # Leadership positions

    # Legislative activity
    sponsored_legislation_count: Optional[int] = None
    sponsored_legislation_url: Optional[str] = None
    cosponsored_legislation_count: Optional[int] = None
    cosponsored_legislation_url: Optional[str] = None

    # Contact information (optional, from detail view)
    official_website_url: Optional[str] = None
    office_address: Optional[str] = None
    phone_number: Optional[str] = None

    # Depiction
    image_url: Optional[str] = None
    image_attribution: Optional[str] = None

    # Sponsorship metadata (when Member represents a sponsor/cosponsor)
    sponsorship_date: Optional[str] = None  # Date they became a sponsor/cosponsor
    sponsorship_withdrawn_date: Optional[str] = None  # Date they withdrew (if applicable)
    is_original_cosponsor: Optional[bool] = None  # True if original cosponsor, False if added later

    # Metadata
    update_date: Optional[str] = None
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BillAction:
    """Represents an action taken on a bill."""
    action_code: Optional[str] = None  # Action code (e.g., "36000", "E30000")
    action_date: Optional[str] = None  # Date of action (ISO format)
    text: Optional[str] = None  # Description of the action
    action_type: Optional[str] = None  # Type of action (e.g., "BecameLaw", "IntroReferral")
    source_system: Optional[Dict[str, Any]] = None  # {"code": int, "name": str}
    committees: List[Dict[str, Any]] = field(default_factory=list)  # Committees involved
    recorded_votes: List[Dict[str, Any]] = field(default_factory=list)  # Recorded votes
    calendar_number: Optional[str] = None  # Calendar number if applicable
    action_time: Optional[str] = None  # Time of action if available
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BillTextVersion:
    type: Optional[str] = None
    url: Optional[str] = None
    date: Optional[str] = None
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VoteMember:
    """Represents how a member voted."""
    bioguide_id: Optional[str] = None
    name: Optional[str] = None  # Member's name
    party: Optional[str] = None  # Party affiliation
    state: Optional[str] = None  # State represented
    vote_cast: Optional[str] = None  # "Yea", "Nay", "Present", "Not Voting"
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Vote:
    """Represents a roll call vote in the House or Senate."""
    congress: int
    session: int
    vote_number: int
    chamber: Optional[str] = None  # "House" or "Senate"

    # Vote metadata
    vote_date: Optional[str] = None  # Date of vote
    vote_type: Optional[str] = None  # "Recorded Vote", "Voice Vote", etc.
    vote_result: Optional[str] = None  # "Passed", "Failed", etc.
    vote_question: Optional[str] = None  # Question being voted on
    vote_desc: Optional[str] = None  # Vote description
    vote_title: Optional[str] = None  # Title of the vote

    # Vote totals
    yea_total: Optional[int] = None
    nay_total: Optional[int] = None
    present_total: Optional[int] = None
    not_voting_total: Optional[int] = None

    # Related legislation
    bill: Optional[Dict[str, Any]] = None  # Bill information if vote is on a bill
    amendment: Optional[Dict[str, Any]] = None  # Amendment information if vote is on an amendment

    # Member votes (populated when fetching member votes)
    members: List[VoteMember] = field(default_factory=list)

    # Metadata
    update_date: Optional[str] = None
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
    policy_area: Optional[str] = None  # dict.get("name") -> "Policy Area Name"

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
    subjects: List[str] = field(default_factory=list)  # List of legislative subject names if fetched
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
