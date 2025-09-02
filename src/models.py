from __future__ import annotations
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
    # Core committees array, as seen in list+detail
    committees: List[Dict[str, Any]] = field(default_factory=list)

    # Detail-only enrichments (map them when present):
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
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    party: Optional[str] = None
    state: Optional[str] = None
    chamber: Optional[str] = None
    is_current: Optional[bool] = None
    roles: List[MemberRole] = field(default_factory=list)
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
class Bill:
    congress: int
    bill_type: str
    bill_number: int
    title: Optional[str] = None
    latest_action: Optional[str] = None
    sponsor: Optional[Dict[str, Any]] = None
    urls: List[str] = field(default_factory=list)
    texts: List[BillTextVersion] = field(default_factory=list)
    api_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
