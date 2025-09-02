#%%
from __future__ import annotations

import os
import json
import random
import time
import logging
from tqdm import tqdm
import email.utils as eut
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional, Tuple, Union, Dict, Any, List, Set, Literal

import requests
from requests import RequestException
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from src.utils import logger_setup
from src.models import (
    Bill, BillTextVersion, Committee, CommitteeMeeting, Hearing, HearingFormat,
    Member, MemberRole, Subcommittee
)

from dotenv import load_dotenv

load_dotenv()

CONGRESS_API_KEY = os.getenv("CONGRESS_API_KEY")


#%%
# ----------------------------------- Dataclass Definitions --------------------------------------#

Entity = Literal["hearing", "committee_meeting", "committee", "bill", "member"]
Predicate = Callable[[Dict[str, Any]], bool]



class CongressAPIClient:
    """
    Typed wrapper for Congress.gov v3 API with retries/backoff and simple rate limiting.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.congress.gov/v3",
        timeout: int = 60,
        min_interval: float = 0.0,  # seconds between requests (politeness throttle)
        max_tries: int = 8,
        backoff_base: float = 0.75,
        backoff_cap: float = 30.0,
        limit: int = 250,
        log_level: int = logging.INFO
    ):
        self.api_key = api_key or os.getenv("CONGRESS_API_KEY") or os.getenv("CONGRESS_DOT_GOV_API_KEY")
        if not self.api_key:
            raise ValueError("Congress.gov API key not provided. Set CONGRESS_API_KEY env var or pass api_key=...")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, application/xml;q=0.9, */*;q=0.8"
        })

        # backoff/limits
        self.min_interval = float(min_interval)
        self._last_call_ts = 0.0
        self.max_tries = int(max_tries)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.limit = int(limit)
        self.logger = logger_setup(logger_name="Congress API Client", log_level=log_level)

    # ------------- throttling -------------
    def _gate(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.time()
        wait = self.min_interval - (now - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.time()

    # ------------- backoff helpers -------------
    @staticmethod
    def _parse_retry_after(value: str) -> float:
        """Return seconds to sleep from a Retry-After header (seconds or HTTP-date)."""
        if not value:
            return 0.0
        try:
            return float(value)
        except ValueError:
            try:
                dt = eut.parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                return 0.0


            
    @staticmethod
    def _parse_payload(resp: requests.Response) -> Dict[str, Any]:
        """
        Parse a Congress.gov payload that may be JSON or XML.
        Try JSON first, then XML via xmltodict; return a plain dict.
        """
        # 1) Try JSON
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError):
            pass

        # 2) Fallback to XML
        try:
            import xmltodict  # lazy import so it's optional until needed
        except Exception as e:
            raise RuntimeError(
                "Response appears to be XML, but 'xmltodict' is not installed. "
                "Install it with `pip install xmltodict`."
            ) from e

        parsed = xmltodict.parse(resp.text)
        # Ensure a plain dict (no OrderedDict) via JSON round-trip
        return json.loads(json.dumps(parsed))

    def _sleep_backoff(self, attempt: int) -> None:
        # Full jitter: sleep in [0, min(cap, base * 2**attempt)]
        upper = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
        time.sleep(random.uniform(0, upper))

    def _request_with_backoff(self, method: str, url: str, *, params: dict | None = None) -> requests.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_tries):
            try:
                self._gate()
                resp = self.session.request(method, url, params=params, timeout=self.timeout)
                if 200 <= resp.status_code < 300:
                    return resp
                if resp.status_code in (429, 500, 502, 503, 504):
                    ra = self._parse_retry_after(resp.headers.get("Retry-After", ""))
                    if ra > 0:
                        time.sleep(ra)
                    else:
                        self._sleep_backoff(attempt)
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
                    continue
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout, RequestException) as e:
                last_exc = e
                self._sleep_backoff(attempt)
                continue
        if isinstance(last_exc, requests.HTTPError):
            raise last_exc
        raise requests.RetryError(f"Failed after {self.max_tries} attempts: {url}")  # type: ignore

    # ------------- core request helpers -------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        p = {"api_key": self.api_key, "limit": self.limit}
        if params:
            p.update({k: v for k, v in params.items() if v is not None})
        resp = self._request_with_backoff("GET", url, params=p)
        return self._parse_payload(resp) 

    def _extract_items(self, block) -> list:
        """
        Normalize Congress.gov list payloads:
        - {"item": [...]} -> [...]
        - {"item": {...}} -> [{...}]
        - [...]            -> [...]
        - {"items": [...]} -> [...]
        - {"items": {...}} -> [{...}]
        - None/other       -> []
        """
        if block is None:
            return []
        if isinstance(block, list):
            return block
        if isinstance(block, dict):
            item = block.get("item")
            items = block.get("items")
            if isinstance(item, list):
                return item
            if isinstance(item, dict):
                return [item]
            if isinstance(items, list):
                return items
            if isinstance(items, dict):
                return [items]
        return []
    
    # inside CongressAPI
    def _url_with_key(self, url: Optional[str]) -> Optional[str]:
        """Return URL with api_key added if it's an api.congress.gov link; pass through others/None."""
        if not url:
            return url
        u = urlparse(url)
        if u.netloc != "api.congress.gov":
            # Don't append keys to non-API assets like PDFs on www.congress.gov
            return url
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        if "api_key" not in q:
            q["api_key"] = self.api_key
        new_q = urlencode(q, doseq=True)
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))
    
    def _paged(self, first_path: str, data_key: str, params: Optional[Dict[str, Any]] = None):
        def _unwrap_root(d: dict) -> dict:
            # If XML, root may be wrapped as {'root': {...}}
            if not isinstance(d, dict):
                return d
            if 'root' in d and isinstance(d['root'], dict):
                return d['root']
            return d

        self.logger.info(f"Starting pagination for path: {first_path}")
        data = self._get(first_path, params=params)
        data = _unwrap_root(data)
        self.logger.info(f"First page data structure: {list(data.keys())}")

        # First page items
        items = self._extract_items(data.get(data_key))
        self.logger.debug(f"First page found {len(list(items))} items")
        for item in items:
            yield item

        # Check for next page
        pagination = data.get("pagination")
        self.logger.debug(f"First page pagination structure: {repr(pagination)}")
        
        # Handle empty/missing pagination
        if not pagination or pagination == {}:
            self.logger.info("No pagination or empty pagination. Stopping.")
            return
            
        next_url = None
        if isinstance(pagination, dict):
            next_url = pagination.get("next")
        
        seen_urls = set()
        while next_url:
            self.logger.debug(f"Fetching next page: {next_url}")
            if next_url in seen_urls:
                self.logger.warning(f"Warning: Detected repeated next_url. Breaking loop.")
                break
                
            seen_urls.add(next_url)
            next_url = self._url_with_key(next_url)
            
            resp2 = self._request_with_backoff("GET", next_url)
            data = self._parse_payload(resp2)
            data = _unwrap_root(data)
            self.logger.debug(f"Next page data structure: {list(data.keys())}")
            
            items = self._extract_items(data.get(data_key))
            self.logger.debug(f"Page found {len(list(items))} items")
            for item in items:
                yield item
                
            pagination = data.get("pagination")
            self.logger.debug(f"Page pagination structure: {repr(pagination)}")
            
            if not pagination or pagination == {}:
                self.logger.info("No more pages (empty pagination)")
                break
                
            next_url = None
            if isinstance(pagination, dict):
                next_url = pagination.get("next")


    def iter_entities(
        self,
        entity: Entity,
        *,
        chamber: Optional[str] = None,
        congress: Optional[int] = None,
        congress_range: Optional[Tuple[int, int]] = None,  # for list-by-congress entities
        hydrate: bool = False,
        where: Optional[Predicate] = None,                 # receives a dict (list item or hydrated detail)
        # bills-specific optional params
        bill_type: Optional[str] = None,
        introduced_start: Optional[str] = None,
        introduced_end: Optional[str] = None,
        # members-specific optional params
        state: Optional[str] = None,
        district: Optional[str] = None,
        current: Optional[bool] = None,
    ) -> Iterator[Union[Dict[str, Any], Any]]:
        """
        Stream entities with optional detail hydration and predicate filtering.

        - entity: one of "hearing", "committee_meeting", "committee", "bill", "member"
        - where: a function(dict) -> bool; applied to list item (fast) or detail (if hydrate=True)
        - hydrate: if True, fetch detail and return a typed object (Hearing, CommitteeMeeting, Committee, Bill, Member)
                otherwise return the raw list item dict (fast).
        - congress_range: (start, end), inclusive, for entities that list by congress (hearing/committee_meeting/bill)
        """
        def _range(cg: Optional[int], cgr: Optional[Tuple[int, int]]) -> List[int]:
            if cgr and len(cgr) == 2:
                a, b = cgr
                if a > b: a, b = b, a
                return list(range(a, b + 1))
            return [cg] if cg else []

        # Map list streaming per entity
        def _iter_list_items_for_congress(target_congress: int) -> Iterator[Dict[str, Any]]:
            if entity == "hearing":
                path = (f"hearing/{target_congress}/{chamber}" if chamber else f"hearing/{target_congress}")
                yield from self._paged(path, data_key="hearings")
            elif entity == "committee_meeting":
                path = (f"committee-meeting/{target_congress}/{chamber}" if chamber else f"committee-meeting/{target_congress}")
                yield from self._paged(path, data_key="committeeMeetings")
            elif entity == "bill":
                if bill_type:
                    path = f"bill/{target_congress}/{bill_type}"
                else:
                    path = f"bill/{target_congress}"
                params = {}
                if introduced_start: params["introducedDateStart"] = introduced_start
                if introduced_end:   params["introducedDateEnd"]   = introduced_end
                yield from self._paged(path, data_key="bills", params=params)
            else:
                raise ValueError(f"Entity '{entity}' does not support congress-scoped listing.")

        def _iter_list_items_general() -> Iterator[Dict[str, Any]]:
            if entity == "committee":
                path = ("committee" if not (congress and chamber) else f"committee/{congress}/{chamber}")
                yield from self._paged(path, data_key="committees")
            elif entity == "member":
                if congress and chamber:
                    path, params = f"member/{congress}/{chamber}", None
                else:
                    path, params = "member", {
                        "congress": congress,
                        "chamber": chamber,
                        "state": state,
                        "district": district,
                        "currentMember": str(current).lower() if isinstance(current, bool) else None,
                    }
                yield from self._paged(path, data_key="members", params=params)
            else:
                raise ValueError(f"Unhandled general list entity '{entity}'.")

        # Choose listing strategy
        list_stream: Iterator[Dict[str, Any]]
        if entity in ("hearing", "committee_meeting", "bill"):
            congresses = _range(congress, congress_range) or ([congress] if congress else [])
            if not congresses:
                raise ValueError(f"Provide congress or congress_range for entity '{entity}'.")
            def _chain():
                for cg in congresses:
                    for item in _iter_list_items_for_congress(cg):
                        yield item
            list_stream = _chain()
        else:
            list_stream = _iter_list_items_general()

        # Detail fetchers (typed) per entity
        def _hydrate(item: Dict[str, Any]):
            if entity == "hearing":
                jn, cg, ch = item.get("jacketNumber"), item.get("congress"), (item.get("chamber") or chamber or "").lower()
                if not (jn and cg and ch): return None
                return self.get_hearing(cg, ch, jn)
            if entity == "committee_meeting":
                ev, cg, ch = item.get("eventId"), item.get("congress"), (item.get("chamber") or chamber or "").lower()
                if not (ev and cg and ch): return None
                return self.get_committee_meeting(cg, ch, ev)
            if entity == "committee":
                ch = (item.get("chamber") or chamber or "").lower()
                sc = item.get("systemCode")
                if not (sc and ch): return None
                return self.get_committee(ch, sc)
            if entity == "bill":
                cg = item.get("congress")
                bt = item.get("type") or item.get("billType")
                num = item.get("number")
                if not (cg and bt and num): return None
                return self.get_bill(cg, bt, num)
            if entity == "member":
                bid = item.get("bioguideId")
                if not bid: return None
                return self.get_member(bid)
            return None

        # Stream + (optional) filter + (optional) hydrate
        for it in list_stream:
            # If filtering without hydration, pass the list item dict to predicate
            if where and not hydrate and not where(it):
                continue

            if hydrate:
                full = _hydrate(it)
                if full is None:
                    continue
                # If filtering with hydration, convert to a dict-like view for the predicate
                if where:
                    # Use the already-available .raw when present, else build a minimal dict
                    raw_like = getattr(full, "raw", None)
                    probe = raw_like if isinstance(raw_like, dict) else (
                        full.__dict__ if hasattr(full, "__dict__") else {}
                    )
                    if not where(probe):
                        continue
                yield full
            else:
                yield it

    def _extract_text_list(self, block):
        """Return a flat list of strings for blocks that arrive as list/dict."""
        out = []
        for it in self._extract_items(block):
            if isinstance(it, str):
                out.append(it)
            elif isinstance(it, dict):
                # prefer 'name' or 'text' keys if they exist
                out.append(it.get("name") or it.get("text") or str(it))
            else:
                out.append(str(it))
        return out

    # ------------- committees -------------
    def get_committees(self, congress: Optional[int] = None, chamber: Optional[str] = None) -> List[Committee]:
        path = "committee" if not (congress and chamber) else f"committee/{congress}/{chamber}"
        items = list(self._paged(path, data_key="committees"))
        out: List[Committee] = []
        for it in items:
            subs = [
                Subcommittee(system_code=sc.get("systemCode"), 
                             name=sc.get("name"), 
                             raw=sc)
                for sc in self._extract_items(it.get("subcommittees"))
            ] or []
            parent = it.get("parent") or {}
            out.append(Committee(
                system_code=it.get("systemCode"),
                name=it.get("name"),
                chamber=it.get("chamber"),
                committee_type=it.get("committeeTypeCode"),
                parent_system_code=parent.get("systemCode"),
                parent_name=parent.get("name"),
                subcommittees=subs,
                api_url=self._url_with_key(it.get("url")),
                raw=it,
            ))
        return out

    def get_committee(self, chamber: str, system_code: str) -> Committee:
        data = self._get(f"committee/{chamber}/{system_code}")
        c = data.get("committee", {})
        subs = [
                Subcommittee(system_code=sc.get("systemCode"), 
                             name=sc.get("name"), 
                             raw=sc)
                for sc in self._extract_items(c.get("subcommittees"))
            ] or []
        parent = c.get("parent") or {}
        return Committee(
            system_code=c.get("systemCode"),
            name=c.get("name"),
            chamber=None,
            committee_type=None,
            parent_system_code=parent.get("systemCode"),
            parent_name=parent.get("name"),
            subcommittees=subs,
            api_url=self._url_with_key(c.get("url")),
            raw=c,
        )

    # ------------- hearings -------------
    def get_hearings(self, congress: Optional[int] = None, chamber: Optional[str] = None) -> List[Hearing]:
        if congress and chamber:
            path = f"hearing/{congress}/{chamber}"
        elif congress:
            path = f"hearing/{congress}"
        else:
            path = "hearing"
        items = list(self._paged(path, data_key="hearings"))
        out: List[Hearing] = []
        for it in items:
            formats = [HearingFormat(type=f.get("type"), url=f.get("url"))
                       for f in self._extract_items(it.get("formats"))]
            try:
                jacket_number = int(it.get("jacketNumber"))
            except (ValueError, TypeError):
                jacket_number = str(it.get("jacketNumber"))
            out.append(Hearing(
                jacket_number=jacket_number,
                title=it.get("title"),
                congress=it.get("congress"),
                chamber=it.get("chamber"),
                citation=it.get("citation"),
                committees=[{"name": x.get("name"), "systemCode": x.get("systemCode")}
                            for x in self._extract_items(it.get("committees"))],
                dates=[d.get("date") for d in self._extract_items(it.get("dates"))],
                formats=formats,
                api_url=self._url_with_key(it.get("url")),
                raw=it,
            ))
        return out

    def get_hearing(self, congress: int, chamber: str, jacket_number: int) -> Hearing:
        h = self._get(f"hearing/{congress}/{chamber}/{jacket_number}").get("hearing", {})
        formats = [HearingFormat(type=f.get("type"), url=f.get("url"))
                   for f in self._extract_items(h.get("formats"))]
        try:
            jacket_number = int(h.get("jacketNumber"))
        except (ValueError, TypeError):
            jacket_number = str(h.get("jacketNumber"))
        return Hearing(
            jacket_number=jacket_number,
            title=h.get("title"),
            congress=h.get("congress"),
            chamber=h.get("chamber"),
            citation=h.get("citation"),
            committees=[{"name": x.get("name"), "systemCode": x.get("systemCode")}
                        for x in self._extract_items(h.get("committees"))],
            dates=[d.get("date") for d in self._extract_items(h.get("dates"))],
            formats=formats,
            api_url=self._url_with_key(h.get("url")),
            raw=h,
        )

    # ------------- committee meetings -------------
    def get_committee_meetings(self, congress: Optional[int] = None, chamber: Optional[str] = None) -> List[CommitteeMeeting]:
        if congress and chamber:
            path = f"committee-meeting/{congress}/{chamber}"
        elif congress:
            path = f"committee-meeting/{congress}"
        else:
            path = "committee-meeting"
        items = list(self._paged(path, data_key="committeeMeetings"))
        out: List[CommitteeMeeting] = []
        for it in items:
            out.append(CommitteeMeeting(
                event_id=it.get("eventId"),
                type=it.get("type"),
                title=it.get("title"),
                meeting_status=it.get("meetingStatus"),
                date=it.get("date"),
                chamber=it.get("chamber"),
                committees=[{"name": x.get("name"), "systemCode": x.get("systemCode")}
                            for x in self._extract_items(it.get("committees"))],
                api_url=self._url_with_key(it.get("url")),
                raw=it,
            ))
        return out

    def get_committee_meeting(self, congress: int, chamber: str, event_id: int) -> CommitteeMeeting:
        m = self._get(f"committee-meeting/{congress}/{chamber}/{event_id}").get("committeeMeeting", {})

        # core committee array (name + systemCode pairs)
        committees = [
            {"name": x.get("name"), "systemCode": x.get("systemCode")}
            for x in self._extract_items(m.get("committees"))
        ]

        # Optional blocks frequently present in detail payloads
        witnesses = [w for w in self._extract_items(m.get("witnesses"))] or []
        meeting_docs = [d for d in self._extract_items(m.get("meetingDocuments"))] or []
        videos = [v for v in self._extract_items(m.get("videos"))] or []
        related_bills = [b for b in self._extract_items(m.get("bills"))] or []
        related_noms = [n for n in self._extract_items(m.get("nominations"))] or []
        related_treaties = [t for t in self._extract_items(m.get("treaties"))] or []

        return CommitteeMeeting(
            event_id=m.get("eventId"),
            type=m.get("type"),
            title=m.get("title"),
            meeting_status=m.get("meetingStatus"),
            date=m.get("date"),
            chamber=m.get("chamber"),
            committees=committees,

            location=m.get("location"),
            room=m.get("room"),
            witnesses=witnesses,
            documents=meeting_docs,
            videos=videos,
            related_bills=related_bills,
            related_nominations=related_noms,
            related_treaties=related_treaties,

            api_url=self._url_with_key(m.get("url")),
            raw=m,
        )


    # ------------- members -------------
    def get_members(
        self,
        congress: Optional[int] = None,
        chamber: Optional[str] = None,
        state: Optional[str] = None,
        district: Optional[str] = None,
        current: Optional[bool] = None,
    ) -> List[Member]:
        if congress and chamber:
            path = f"member/{congress}/{chamber}"
            params = None
        else:
            path = "member"
            params = {
                "congress": congress,
                "chamber": chamber,
                "state": state,
                "district": district,
                "currentMember": str(current).lower() if isinstance(current, bool) else None,
            }
        items = list(self._paged(path, data_key="members", params=params))
        out: List[Member] = []
        for it in items:
            roles = [
                MemberRole(
                    congress=r.get("congress"),
                    chamber=r.get("chamber"),
                    title=r.get("title"),
                    state=r.get("state"),
                    district=r.get("district"),
                    start=r.get("startYear"),
                    end=r.get("endYear"),
                    raw=r,
                )
                for r in self._extract_items(it.get("roles"))
            ]
            out.append(
                Member(
                    bioguide_id=it.get("bioguideId"),
                    first_name=it.get("firstName"),
                    last_name=it.get("lastName"),
                    full_name=it.get("name"),
                    party=it.get("party"),
                    state=it.get("state"),
                    chamber=it.get("chamber"),
                    is_current=it.get("isCurrent"),
                    roles=roles,
                    api_url=self._url_with_key(it.get("url")),
                    raw=it,
                )
            )
        return out

    def get_member(self, bioguide_id: str) -> Member:
        m = self._get(f"member/{bioguide_id}").get("member", {})
        roles = [
            MemberRole(
                congress=r.get("congress"), chamber=r.get("chamber"),
                title=r.get("title"), state=r.get("state"), district=r.get("district"),
                start=r.get("startYear"), end=r.get("endYear"), raw=r
            )
            for r in self._extract_items(m.get("roles"))
        ]
        return Member(
            bioguide_id=m.get("bioguideId"),
            first_name=m.get("firstName"),
            last_name=m.get("lastName"),
            full_name=m.get("name"),
            party=m.get("party"),
            state=m.get("state"),
            chamber=m.get("chamber"),
            is_current=m.get("isCurrent"),
            roles=roles,
            api_url=self._url_with_key(m.get("url")),
            raw=m,
        )

    # ------------- legislation (bills) -------------
    def get_bills(
        self,
        congress: Optional[int] = None,
        bill_type: Optional[str] = None,       # "hr", "s", "sjres", etc.
        query: Optional[str] = None,           # if/when supported on list
        introduced_start: Optional[str] = None,
        introduced_end: Optional[str] = None,
    ) -> List[Bill]:
        if congress and bill_type:
            path = f"bill/{congress}/{bill_type}"
            params = {}
        elif congress:
            path = f"bill/{congress}"
            params = {}
        else:
            path = "bill"
            params = {}
        if query:
            params["query"] = query
        if introduced_start:
            params["introducedDateStart"] = introduced_start
        if introduced_end:
            params["introducedDateEnd"] = introduced_end

        items = list(self._paged(path, data_key="bills", params=params))
        out: List[Bill] = []
        for it in items:
            texts = [
                BillTextVersion(type=tv.get("type"), url=tv.get("url"), date=tv.get("date"), raw=tv)
                for tv in self._extract_items(it.get("textVersions"))
            ]
            out.append(
                Bill(
                    congress=it.get("congress"),
                    bill_type=it.get("type") or it.get("billType"),
                    bill_number=it.get("number"),
                    title=it.get("title"),
                    latest_action=(it.get("latestAction") or {}).get("text"),
                    sponsor=it.get("sponsor"),
                    urls=[u for u in [it.get("url")] if u],
                    texts=texts,
                    api_url=self._url_with_key(it.get("url")),
                    raw=it,
                )
            )
        return out

    def get_bill(self, congress: int, bill_type: str, bill_number: int) -> Bill:
        b = self._get(f"bill/{congress}/{bill_type}/{bill_number}").get("bill", {})
        texts = [
            BillTextVersion(type=tv.get("type"), url=tv.get("url"), date=tv.get("date"), raw=tv)
            for tv in self._extract_items(b.get("textVersions"))
        ]
        return Bill(
            congress=b.get("congress"),
            bill_type=b.get("type") or b.get("billType"),
            bill_number=b.get("number"),
            title=b.get("title"),
            latest_action=(b.get("latestAction") or {}).get("text"),
            sponsor=b.get("sponsor"),
            urls=[u for u in [b.get("url")] if u],
            texts=texts,
            api_url=self._url_with_key(b.get("url")),
            raw=b,
        )

#%%

if __name__ == "__main__":
    client = CongressAPIClient(
        api_key=CONGRESS_API_KEY,
        timeout=60,
        min_interval=0.0,   # set e.g. 0.1 to cap at ~10 rps
        max_tries=8,          # retry attempts for 429/5xx/timeouts
        backoff_base=0.75,  # base backoff seconds
        backoff_cap=30.0    # max backoff sleep
    )

    
    TARGETS = {"hsas00", "ssas00", "ssfr00", "hsfa00"}

    all_meetings = client.get_committee_meetings(congress=118, chamber="house")
    meetings_to_keep = []

    #%%
    for i, h in enumerate(tqdm(all_meetings)):
        full = client.get_meeting(h.congress, h.chamber.lower(), h.jacket_number)
        if any(c["systemCode"] in TARGETS for c in full.committees):
            for f in full.formats:
                if f.type in ("PDF", "Formatted Text"):
                    meetings_to_keep.append({
                        "title": full.title,
                        "url": f.url,
                        "committee": full.committees
                    })
                    print(full.title, f.url)
                    if i >= 10:
                        break
# %%
