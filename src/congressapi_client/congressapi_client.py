#%%
from __future__ import annotations

import email.utils as eut
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import (Any, Callable, Dict, Iterator, List, Literal, Optional,
                    Set, Tuple, Union)
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests import RequestException

from .models import (Amendment, Bill, BillTextVersion, Committee,
                     CommitteeMeeting, Hearing, HearingFormat, Member,
                     MemberRole, Subcommittee)
from .utils import logger_setup

#%%
# ----------------------------------- Dataclass Definitions --------------------------------------#

Entity = Literal["hearing", "committee_meeting", "committee", "bill", "member", "amendment"]
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
        min_interval: float = 0.1,  # politeness throttle
        max_tries: int = 8,
        backoff_base: float = 0.75,  # More conservative backoff
        backoff_cap: float = 60.0,  # Higher cap for severe rate limiting
        limit: int = 250, # number to return
        log_level: int = logging.INFO,
        req_per_hour: int = 5000,
        rph_margin = 0.01, # reduce max requests per hour by 1% (ie 50) given requests tend to be bundled
        sleep_minutes: int = 15,  # sleep time when rate limit exhausted
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

        # set rate limit throttling
        if not (0.0 <= rph_margin < 1.0):
            raise ValueError(f"rph_margin must be in [0,1). Got {rph_margin}.")
        self.rph_margin = float(rph_margin)
        self.req_per_hour = int(req_per_hour)

        effective_limit = max(1, int(self.req_per_hour * (1.0 - self.rph_margin)))
        self.hourly_capacity = effective_limit                    # integer bucket size
        self.hourly_refill_rate = float(effective_limit) / 3600.0 # tokens per second
        self.sleep_minutes = int(sleep_minutes)

        # monotonic clock for continuous upward counting
        self._last_call = time.monotonic()
        self._last_refill = time.monotonic()
        self._tokens = float(self.hourly_capacity)  # start full


    # ------------- throttling -------------
    def _gate(self) -> None:
        # politeness throttle
        if self.min_interval > 0.0:
            now_m = time.monotonic()
            last = getattr(self, "_last_call", now_m)
            wait = self.min_interval - (now_m - last)
            if wait > 0:
                time.sleep(wait)
                now_m = time.monotonic()
            self._last_call = now_m

        # hourly token bucket (api limits at 5000/hr, but buffer built in)
        if self.hourly_refill_rate <= 0 or self.hourly_capacity <= 0:
            return

        prev_refill = getattr(self, "_last_refill", time.monotonic())
        now_m = time.monotonic()

        # Refill based on time since last refill (do not mutate _last_refill yet)
        elapsed = now_m - prev_refill
        tokens = min(self.hourly_capacity, self._tokens + elapsed * self.hourly_refill_rate)

        # If short a token, sleep for a substantial period (15-20 min) to let many tokens accumulate
        if tokens < 1.0:
            # Sleep for 15-20 minutes to let a decent number of tokens accumulate
            sleep_s = self.sleep_minutes * 60
            expected_tokens = sleep_s * self.hourly_refill_rate
            self.logger.info(f"Hourly budget exhausted; sleeping {self.sleep_minutes} minutes to accumulate ~{expected_tokens:.0f} requests.")
            time.sleep(sleep_s)
            now_m = time.monotonic()
            elapsed = now_m - prev_refill
            tokens = min(self.hourly_capacity, self._tokens + elapsed * self.hourly_refill_rate)

        # Consume one token and commit state
        self._tokens = max(0.0, tokens - 1.0)
        self._last_refill = now_m


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
        sleep_time = random.uniform(0, upper)
        self.logger.info(f"Backoff: sleeping for {sleep_time:.2f} seconds on attempt {attempt+1} (max {self.max_tries})")
        time.sleep(sleep_time)

    def _request_with_backoff(self, method: str, url: str, *, params: dict | None = None) -> requests.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_tries):
            try:
                self._gate()
                resp = self.session.request(method, url, params=params, timeout=self.timeout)
                if 200 <= resp.status_code < 300:
                    return resp
                if resp.status_code in (429, 500, 502, 503, 504):
                    self.logger.warning(f"API request to {url} failed with status {resp.status_code}: {resp.text[:200]}")
                    ra = self._parse_retry_after(resp.headers.get("Retry-After", ""))
                    if ra > 0:
                        self.logger.info(f"Sleeping for {ra:.2f} seconds.")
                        time.sleep(ra)
                    else:
                        self._sleep_backoff(attempt)
                    last_exc = requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
                    continue
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout, RequestException) as e:
                last_exc = e
                self.logger.warning(f"Request error on attempt {attempt+1}/{self.max_tries}: {type(e).__name__}: {e}")
                self._sleep_backoff(attempt)
                continue
        if isinstance(last_exc, requests.HTTPError):
            raise last_exc
        raise requests.RequestException(f"Failed after {self.max_tries} attempts: {url}")

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

    def _dict_to_member(self, member_dict: Dict[str, Any], *,
                       sponsorship_date: Optional[str] = None,
                       sponsorship_withdrawn_date: Optional[str] = None,
                       is_original_cosponsor: Optional[bool] = None) -> Member:
        """Convert a member dictionary from API response to Member object."""
        return Member(
            bioguide_id=member_dict.get("bioguideId"),
            first_name=member_dict.get("firstName"),
            middle_name=member_dict.get("middleName"),
            last_name=member_dict.get("lastName"),
            full_name=member_dict.get("fullName"),
            party=member_dict.get("party"),
            state=member_dict.get("state"),
            district=member_dict.get("district"),
            # Add sponsorship metadata if provided
            sponsorship_date=sponsorship_date,
            sponsorship_withdrawn_date=sponsorship_withdrawn_date,
            is_original_cosponsor=is_original_cosponsor,
            api_url=self._url_with_key(member_dict.get("url")),
            raw=member_dict
        )

    def _paged(self, first_path: str, data_key: str, params: Optional[Dict[str, Any]] = None):
        def _unwrap_root(d: dict) -> dict:
            # If XML, root may be wrapped as {'root': {...}}
            if not isinstance(d, dict):
                return d
            if 'root' in d and isinstance(d['root'], dict):
                return d['root']
            return d

        self.logger.debug(f"Starting pagination for path: {first_path}")
        data = self._get(first_path, params=params)
        data = _unwrap_root(data)
        self.logger.debug(f"First page data structure: {list(data.keys())}")

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
        include_cosponsors: bool = False,  # For bills, whether to fetch full cosponsors list during hydration
        # amendments-specific optional params
        amendment_type: Optional[str] = None,  # "hamdt", "samdt", etc.
        # members-specific optional params
        state: Optional[str] = None,
        district: Optional[str] = None,
        current: Optional[bool] = None,
        # error handling
        continue_on_error: bool = True,  # If True, log errors and continue; if False, raise on first error
    ) -> Iterator[Union[Dict[str, Any], Any]]:
        """
        Stream entities with optional detail hydration and predicate filtering.

        - entity: one of "hearing", "committee_meeting", "committee", "bill", "member", "amendment"
        - where: a function(dict) -> bool; applied to list item (fast) or detail (if hydrate=True)
        - hydrate: if True, fetch detail and return a typed object (Hearing, CommitteeMeeting, Committee, Bill, Member, Amendment)
                otherwise return the raw list item dict (fast).
        - congress_range: (start, end), inclusive, for entities that list by congress (hearing/committee_meeting/bill/amendment)
        - include_cosponsors: for bills/amendments, whether to fetch full cosponsors list during hydration (slower but complete)
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
            elif entity == "amendment":
                if amendment_type:
                    path = f"amendment/{target_congress}/{amendment_type}"
                else:
                    path = f"amendment/{target_congress}"
                params = {}
                yield from self._paged(path, data_key="amendments", params=params)
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
        if entity in ("hearing", "committee_meeting", "bill", "amendment"):
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
                return self.get_bill(cg, bt, num, hydrate=include_cosponsors)
            if entity == "member":
                bid = item.get("bioguideId")
                if not bid: return None
                return self.get_member(bid)
            if entity == "amendment":
                cg = item.get("congress")
                at = item.get("type")
                num = item.get("number")
                if not (cg and at and num): return None
                return self.get_amendment(cg, at, num, hydrate=include_cosponsors)
            return None

        # Stream + (optional) filter + (optional) hydrate
        for it in list_stream:
            # If filtering without hydration, pass the list item dict to predicate
            if where and not hydrate and not where(it):
                continue

            if hydrate:
                try:
                    full = _hydrate(it)
                    if full is None:
                        continue
                except (requests.RequestException, requests.HTTPError) as e:
                    if continue_on_error:
                        # Log the error but continue processing other items
                        self.logger.error(f"Failed to hydrate {entity} {it}: {e}")
                        continue
                    else:
                        raise  # Re-raise the exception to stop processing
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
    def get_committees(
        self,
        congress: Optional[int] = None,
        chamber: Optional[str] = None,
        *,
        limit: Optional[int] = None  # Maximum number of committees to return (None = all available)
    ) -> List[Committee]:
        path = "committee" if not (congress and chamber) else f"committee/{congress}/{chamber}"
        items = list(self._paged(path, data_key="committees"))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

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
        name = c.get("name")
        if name is None:
            history = c.get("history", [])
            name = next((h.get("libraryOfCongressName") for h in history if h.get("startDate") and not h.get("endDate")), None)
        return Committee(
            system_code=c.get("systemCode"),
            name=name,
            chamber=None,
            committee_type=None,
            parent_system_code=parent.get("systemCode"),
            parent_name=parent.get("name"),
            subcommittees=subs,
            api_url=self._url_with_key(c.get("url")),
            raw=c,
        )

    # ------------- hearings -------------
    def get_hearings(
        self,
        congress: Optional[int] = None,
        chamber: Optional[str] = None,
        *,
        limit: Optional[int] = None  # Maximum number of hearings to return (None = all available)
    ) -> List[Hearing]:
        if congress and chamber:
            path = f"hearing/{congress}/{chamber}"
        elif congress:
            path = f"hearing/{congress}"
        else:
            path = "hearing"
        items = list(self._paged(path, data_key="hearings"))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

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
    def get_committee_meetings(
        self,
        congress: Optional[int] = None,
        chamber: Optional[str] = None,
        *,
        limit: Optional[int] = None  # Maximum number of committee meetings to return (None = all available)
    ) -> List[CommitteeMeeting]:
        if congress and chamber:
            path = f"committee-meeting/{congress}/{chamber}"
        elif congress:
            path = f"committee-meeting/{congress}"
        else:
            path = "committee-meeting"
        items = list(self._paged(path, data_key="committeeMeetings"))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

        out: List[CommitteeMeeting] = []
        for it in items:
            out.append(CommitteeMeeting(
                event_id=it.get("eventId"),
                type=it.get("type"),
                title=it.get("title"),
                meeting_status=it.get("meetingStatus"),
                date=it.get("date"),
                chamber=it.get("chamber"),
                congress=it.get("congress"),
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
            congress=m.get("congress"),
            committees=committees,

            location=m.get("location"),
            room=m.get("room"),
            hearing_transcript=m.get("hearingTranscript"),
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
        *,
        limit: Optional[int] = None  # Maximum number of members to return (None = all available)
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

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

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

    # ------------- legislation (bills and amendments) -------------
    def get_bill(self, congress: int, bill_type: str, bill_number: int, *, hydrate: bool = False) -> Bill:
        """
        Fetch detailed information for a specific bill.

        Args:
            congress: Congress number (e.g., 117)
            bill_type: Bill type ("hr", "s", "hjres", "sjres", etc.)
            bill_number: Bill number
            hydrate: If True, fetch additional related data like full cosponsors list
        """
        # Ensure bill_type is lowercase for API endpoint
        bill_type_lower = bill_type.lower()
        b = self._get(f"bill/{congress}/{bill_type_lower}/{bill_number}").get("bill", {})
        texts = [
            BillTextVersion(type=tv.get("type"), url=tv.get("url"), date=tv.get("date"), raw=tv)
            for tv in self._extract_items(b.get("textVersions"))
        ]

        # Extract cosponsorship info from the bill data
        cosponsors_info = b.get("cosponsors", {})
        cosponsors_count = cosponsors_info.get("count")
        cosponsors_count_including_withdrawn = cosponsors_info.get("countIncludingWithdrawnCosponsors")
        cosponsors_url = self._url_with_key(cosponsors_info.get("url"))

        # Extract latest action info
        latest_action_info = b.get("latestAction", {})
        latest_action_text = latest_action_info.get("text")
        latest_action_date = latest_action_info.get("actionDate")

        # Extract policy area
        policy_area_dict = b.get("policyArea")
        policy_area = policy_area_dict.get("name") if policy_area_dict else None

        # Extract related content URLs and counts
        actions_info = b.get("actions", {})
        amendments_info = b.get("amendments", {})
        committees_info = b.get("committees", {})
        related_bills_info = b.get("relatedBills", {})
        subjects_info = b.get("subjects", {})
        summaries_info = b.get("summaries", {})
        titles_info = b.get("titles", {})

        # Handle sponsors (consolidate 'sponsor' and 'sponsors' fields into sponsors list)
        sponsors = []

        # Add from 'sponsors' field (list)
        sponsors_list = self._extract_items(b.get("sponsors"))
        if sponsors_list:
            sponsors.extend([self._dict_to_member(s) for s in sponsors_list])

        # Add from 'sponsor' field (single object) if not already in sponsors list
        sponsor_obj = b.get("sponsor")
        if sponsor_obj and sponsor_obj not in sponsors_list:
            sponsors.append(self._dict_to_member(sponsor_obj))

        # Optionally fetch full cosponsors and amendments lists
        cosponsors = []
        amendments = []
        if hydrate:
            if cosponsors_url:
                cosponsors = self.get_bill_cosponsors(congress, bill_type_lower, bill_number)
            amendments_url = self._url_with_key(amendments_info.get("url"))
            if amendments_url:
                # Fetch full amendment details with sponsors/cosponsors
                amendments = self.get_bill_amendments(congress, bill_type_lower, bill_number, hydrate=True)

        return Bill(
            congress=b.get("congress"),
            bill_type=b.get("type") or b.get("billType"),
            bill_number=b.get("number"),
            title=b.get("title"),
            introduced_date=b.get("introducedDate"),
            origin_chamber=b.get("originChamber"),
            origin_chamber_code=b.get("originChamberCode"),
            latest_action=latest_action_text,
            latest_action_date=latest_action_date,
            sponsors=sponsors,
            policy_area=policy_area,
            laws=self._extract_items(b.get("laws")),
            constitutional_authority_statement=b.get("constitutionalAuthorityStatementText"),
            cbo_cost_estimates=self._extract_items(b.get("cboCostEstimates")),
            committee_reports=self._extract_items(b.get("committeeReports")),
            cosponsors_count=cosponsors_count,
            cosponsors_count_including_withdrawn=cosponsors_count_including_withdrawn,
            cosponsors=cosponsors,
            cosponsors_url=cosponsors_url,
            actions_url=self._url_with_key(actions_info.get("url")),
            actions_count=actions_info.get("count"),
            amendments_url=self._url_with_key(amendments_info.get("url")),
            amendments_count=amendments_info.get("count"),
            committees_url=self._url_with_key(committees_info.get("url")),
            committees_count=committees_info.get("count"),
            related_bills_url=self._url_with_key(related_bills_info.get("url")),
            related_bills_count=related_bills_info.get("count"),
            subjects_url=self._url_with_key(subjects_info.get("url")),
            subjects_count=subjects_info.get("count"),
            summaries_url=self._url_with_key(summaries_info.get("url")),
            summaries_count=summaries_info.get("count"),
            titles_url=self._url_with_key(titles_info.get("url")),
            titles_count=titles_info.get("count"),
            amendments=amendments,
            legislation_url=b.get("legislationUrl"),
            urls=[u for u in [b.get("url")] if u],
            texts=texts,
            update_date=b.get("updateDate"),
            update_date_including_text=b.get("updateDateIncludingText"),
            api_url=self._url_with_key(b.get("url")) or f"{self.base_url}/bill/{congress}/{bill_type_lower}/{bill_number}?api_key={self.api_key}",
            raw=b,
        )

    def get_bills(
        self,
        congress: Optional[int] = None,
        bill_type: Optional[str] = None,       # "hr", "s", "sjres", etc.
        query: Optional[str] = None,           # if/when supported on list
        introduced_start: Optional[str] = None,
        introduced_end: Optional[str] = None,
        *,
        hydrate: bool = False,  # If True, fetch full cosponsors for each bill (much slower)
        hydrate_delay: float = 0.5,  # Seconds to sleep between hydrated requests to avoid rate limits
        limit: Optional[int] = None,  # Maximum number of bills to return (None = all available)
        verbose: bool = False,
        continue_on_error: bool = True  # If True, log errors and continue; if False, raise on first error
    ) -> List[Bill]:
        """
        Fetch a list of bills with optional filtering.

        Args:
            congress: Congress number (e.g., 117, 118)
            bill_type: Bill type ("hr", "s", "hjres", "sjres", etc.)
            query: Search query (if supported)
            introduced_start: Start date for introduced bills (YYYY-MM-DD)
            introduced_end: End date for introduced bills (YYYY-MM-DD)
            hydrate: If True, fetch full bill data for each bill (MUCH SLOWER - makes individual API calls)
            hydrate_delay: Seconds to sleep between hydrated requests (default 0.5s to avoid rate limits)
            limit: Maximum number of bills to return (None = all available)
            verbose: How much logging is done
            continue_on_error: If True, log errors and continue processing; if False, raise on first error

        Returns:
            List of Bill objects. If hydrate=False, only basic fields are populated.
            If hydrate=True, all fields including cosponsors, policy areas, etc. are populated.

        Warning:
            Using hydrate=True is significantly slower as it makes individual API calls for each bill.
            For 100 bills, expect ~50+ seconds due to rate limiting delays.
        """
        if congress and bill_type:
            # Ensure bill_type is lowercase for API endpoint
            bill_type_lower = bill_type.lower()
            path = f"bill/{congress}/{bill_type_lower}"
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

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

        out: List[Bill] = []
        for i, it in enumerate(items):
            # If hydrate=True, fetch the full bill data instead of using the list summary
            if hydrate:
                congress = it.get("congress")
                bill_type = it.get("type") or it.get("billType")
                bill_number = it.get("number")
                if congress and bill_type and bill_number:
                    # Add extra delay for hydrated requests to avoid rate limits
                    # Since we're making individual API calls for each bill
                    if i > 0:  # Don't sleep before first request
                        if verbose:
                            self.logger.info(f"Hydrated request {i+1}/{len(items)}: sleeping {hydrate_delay}s to respect rate limits")
                        time.sleep(hydrate_delay)

                    # Fetch full bill data with hydration (bill_type from API should already be lowercase)
                    try:
                        full_bill = self.get_bill(congress, bill_type, bill_number, hydrate=True)
                        out.append(full_bill)
                    except (requests.RequestException, requests.HTTPError) as e:
                        if continue_on_error:
                            self.logger.error(f"Failed to fetch bill {congress}/{bill_type}/{bill_number}: {e}")
                            continue  # Skip this bill and continue with the next one
                        else:
                            raise  # Re-raise the exception to stop processing
                    continue

            # For non-hydrated requests, create Bill from list summary data (limited fields)
            # Note: Bills list response has limited data compared to individual bill response
            texts = [
                BillTextVersion(type=tv.get("type"), url=tv.get("url"), date=tv.get("date"), raw=tv)
                for tv in self._extract_items(it.get("textVersions"))
            ]

            # Extract latest action info (available in list response)
            latest_action_info = it.get("latestAction", {})
            latest_action_text = latest_action_info.get("text") if latest_action_info else None
            latest_action_date = latest_action_info.get("actionDate") if latest_action_info else None

            # Most detailed fields are NOT available in bills list response
            # They require individual bill API calls (via hydrate=True)
            out.append(
                Bill(
                    congress=it.get("congress"),
                    bill_type=it.get("type") or it.get("billType"),
                    bill_number=it.get("number"),
                    title=it.get("title"),
                    introduced_date=None,  # Not in list response
                    origin_chamber=it.get("originChamber"),
                    origin_chamber_code=it.get("originChamberCode"),
                    latest_action=latest_action_text,
                    latest_action_date=latest_action_date,
                    sponsors=[],   # Not in list response
                    policy_area=None,  # Not in list response
                    laws=[],  # Not in list response
                    cosponsors_count=None,  # Not in list response
                    cosponsors_count_including_withdrawn=None,  # Not in list response
                    cosponsors=[],  # Not in list response
                    cosponsors_url=None,  # Not in list response
                    actions_url=None,  # Not in list response
                    actions_count=None,  # Not in list response
                    committees_url=None,  # Not in list response
                    committees_count=None,  # Not in list response
                    related_bills_url=None,  # Not in list response
                    related_bills_count=None,  # Not in list response
                    subjects_url=None,  # Not in list response
                    subjects_count=None,  # Not in list response
                    summaries_url=None,  # Not in list response
                    summaries_count=None,  # Not in list response
                    titles_url=None,  # Not in list response
                    titles_count=None,  # Not in list response
                    legislation_url=None,  # Not in list response
                    urls=[u for u in [it.get("url")] if u],
                    texts=texts,
                    update_date=it.get("updateDate"),
                    update_date_including_text=it.get("updateDateIncludingText"),
                    api_url=self._url_with_key(it.get("url")),
                    raw=it,
                )
            )
        return out

    def get_bill_cosponsors(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
        *,
        limit: Optional[int] = None  # Maximum number of cosponsors to return (None = all available)
    ) -> List[Member]:
        """Fetch the list of cosponsors for a specific bill."""
        # Ensure bill_type is lowercase for API endpoint
        bill_type_lower = bill_type.lower()
        data = self._get(f"bill/{congress}/{bill_type_lower}/{bill_number}/cosponsors")
        items = self._extract_items(data.get("cosponsors"))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

        cosponsors: List[Member] = []

        for item in items:
            cosponsors.append(self._dict_to_member(
                item,
                sponsorship_date=item.get("sponsorshipDate"),
                sponsorship_withdrawn_date=item.get("sponsorshipWithdrawnDate"),
                is_original_cosponsor=item.get("isOriginalCosponsor")
            ))

        return cosponsors

    def get_bill_amendments(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
        *,
        hydrate: bool = False,  # If True, fetch full amendment details including sponsors/cosponsors
        limit: Optional[int] = None  # Maximum number of amendments to return (None = all available)
    ) -> List[Amendment]:
        """Fetch the list of amendments for a specific bill."""
        # Ensure bill_type is lowercase for API endpoint
        bill_type_lower = bill_type.lower()
        data = self._get(f"bill/{congress}/{bill_type_lower}/{bill_number}/amendments")
        items = self._extract_items(data.get("amendments"))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

        amendments: List[Amendment] = []

        for item in items:
            if hydrate:
                # Fetch full amendment details
                amendment_congress = item.get("congress")
                amendment_type = item.get("type")
                amendment_number = item.get("number")
                if amendment_congress and amendment_type and amendment_number:
                    # Get full amendment details with sponsors/cosponsors
                    full_amendment = self.get_amendment(amendment_congress, amendment_type, amendment_number, hydrate=True)
                    amendments.append(full_amendment)
                    continue

            # For non-hydrated requests, create Amendment from list summary data (limited fields)
            latest_action_info = item.get("latestAction", {})
            latest_action_text = latest_action_info.get("text")
            latest_action_date = latest_action_info.get("actionDate")

            amendments.append(Amendment(
                congress=item.get("congress"),
                amendment_type=item.get("type"),
                amendment_number=item.get("number"),
                description=item.get("description"),
                purpose=item.get("purpose"),
                latest_action=latest_action_text,
                latest_action_date=latest_action_date,
                sponsors=[],  # Not available in amendment list, need individual amendment call
                cosponsors=[],  # Not available in amendment list, need individual amendment call
                cosponsors_count=None,  # Not available in amendment list
                cosponsors_url=None,  # Not available in amendment list
                actions_url=None,  # Not available in amendment list
                actions_count=None,  # Not available in amendment list
                amendments_url=None,  # Not available in amendment list
                amendments_count=None,  # Not available in amendment list
                text_url=None,  # Not available in amendment list
                update_date=item.get("updateDate"),
                api_url=self._url_with_key(item.get("url")),
                raw=item
            ))

        return amendments

    def get_amendment(self, congress: int, amendment_type: str, amendment_number: int, *, hydrate: bool = False) -> Amendment:
        """
        Fetch detailed information for a specific amendment.

        Args:
            congress: Congress number (e.g., 117)
            amendment_type: Amendment type ("hamdt", "samdt", etc.)
            amendment_number: Amendment number
            hydrate: If True, fetch additional data like full cosponsors list
        """
        # Ensure amendment_type is lowercase for API endpoint
        amendment_type_lower = amendment_type.lower()
        a = self._get(f"amendment/{congress}/{amendment_type_lower}/{amendment_number}").get("amendment", {})

        # Extract latest action info
        latest_action_info = a.get("latestAction", {})
        latest_action_text = latest_action_info.get("text")
        latest_action_date = latest_action_info.get("actionDate")

        # Extract related content URLs and counts
        actions_info = a.get("actions", {})
        amendments_info = a.get("amendments", {})  # Amendments to this amendment
        cosponsors_info = a.get("cosponsors", {})
        text_versions_info = a.get("textVersions", {})

        # Handle sponsors (consolidate 'sponsor' and 'sponsors' fields into sponsors list)
        sponsors = []

        # Add from 'sponsors' field (list)
        sponsors_list = self._extract_items(a.get("sponsors"))
        if sponsors_list:
            sponsors.extend([self._dict_to_member(s) for s in sponsors_list])

        # Add from 'sponsor' field (single object) if not already in sponsors list
        sponsor_obj = a.get("sponsor")
        if sponsor_obj and sponsor_obj not in sponsors_list:
            sponsors.append(self._dict_to_member(sponsor_obj))

        # Optionally fetch full cosponsors list
        cosponsors = []
        cosponsors_url = self._url_with_key(cosponsors_info.get("url"))
        if hydrate:
            # Always try to fetch cosponsors when hydrating, even if URL not in response
            try:
                cosponsors = self.get_amendment_cosponsors(congress, amendment_type_lower, amendment_number)
            except Exception as e:
                # If cosponsor fetching fails, log but continue (amendment might have no cosponsors)
                self.logger.debug(f"Could not fetch cosponsors for amendment {congress}/{amendment_type_lower}/{amendment_number}: {e}")
                cosponsors = []

        return Amendment(
            congress=a.get("congress"),
            amendment_type=a.get("type"),
            amendment_number=a.get("number"),
            description=a.get("description"),
            purpose=a.get("purpose"),
            latest_action=latest_action_text,
            latest_action_date=latest_action_date,
            chamber=a.get("chamber"),
            proposed_date=a.get("proposedDate"),
            submitted_date=a.get("submittedDate"),
            amended_bill=a.get("amendedBill"),
            sponsors=sponsors,
            cosponsors=cosponsors,
            cosponsors_count=cosponsors_info.get("count"),
            cosponsors_count_including_withdrawn=cosponsors_info.get("countIncludingWithdrawnCosponsors"),
            cosponsors_url=cosponsors_url,
            actions_url=self._url_with_key(actions_info.get("url")),
            actions_count=actions_info.get("count"),
            amendments_url=self._url_with_key(amendments_info.get("url")),
            amendments_count=amendments_info.get("count"),
            text_url=self._url_with_key(text_versions_info.get("url")),
            text_count=text_versions_info.get("count"),
            update_date=a.get("updateDate"),
            api_url=self._url_with_key(a.get("url")) or f"{self.base_url}/amendment/{congress}/{amendment_type_lower}/{amendment_number}?api_key={self.api_key}",
            raw=a,
        )

    def get_amendment_cosponsors(
        self,
        congress: int,
        amendment_type: str,
        amendment_number: int,
        *,
        limit: Optional[int] = None  # Maximum number of cosponsors to return (None = all available)
    ) -> List[Member]:
        """Fetch the list of cosponsors for a specific amendment."""
        # Ensure amendment_type is lowercase for API endpoint
        amendment_type_lower = amendment_type.lower()
        data = self._get(f"amendment/{congress}/{amendment_type_lower}/{amendment_number}/cosponsors")
        items = self._extract_items(data.get("cosponsors"))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

        cosponsors: List[Member] = []

        for item in items:
            cosponsors.append(self._dict_to_member(
                item,
                sponsorship_date=item.get("sponsorshipDate"),
                sponsorship_withdrawn_date=item.get("sponsorshipWithdrawnDate"),
                is_original_cosponsor=item.get("isOriginalCosponsor")
            ))

        return cosponsors

    def get_amendments(
        self,
        congress: Optional[int] = None,
        amendment_type: Optional[str] = None,  # "hamdt", "samdt", etc.
        *,
        limit: Optional[int] = None  # Maximum number of amendments to return (None = all available)
    ) -> List[Amendment]:
        """Fetch a list of amendments with optional filtering."""
        if congress and amendment_type:
            # Ensure amendment_type is lowercase for API endpoint
            amendment_type_lower = amendment_type.lower()
            path = f"amendment/{congress}/{amendment_type_lower}"
            params = {}
        elif congress:
            path = f"amendment/{congress}"
            params = {}
        else:
            path = "amendment"
            params = {}

        items = list(self._paged(path, data_key="amendments", params=params))

        # Apply limit if specified
        if limit is not None and limit > 0:
            items = items[:limit]

        out: List[Amendment] = []
        for it in items:
            # Extract latest action info (available in list response)
            latest_action_info = it.get("latestAction", {})
            latest_action_text = latest_action_info.get("text") if latest_action_info else None
            latest_action_date = latest_action_info.get("actionDate") if latest_action_info else None

            # Most detailed fields require individual amendment API calls
            out.append(
                Amendment(
                    congress=it.get("congress"),
                    amendment_type=it.get("type"),
                    amendment_number=it.get("number"),
                    description=it.get("description"),
                    purpose=it.get("purpose"),
                    latest_action=latest_action_text,
                    latest_action_date=latest_action_date,
                    sponsors=[],  # Not in list response
                    cosponsors=[],  # Not in list response
                    cosponsors_count=None,  # Not in list response
                    cosponsors_url=None,  # Not in list response
                    actions_url=None,  # Not in list response
                    actions_count=None,  # Not in list response
                    amendments_url=None,  # Not in list response
                    amendments_count=None,  # Not in list response
                    text_url=None,  # Not in list response
                    update_date=it.get("updateDate"),
                    api_url=self._url_with_key(it.get("url")),
                    raw=it,
                )
            )
        return out


#%%

if __name__ == "__main__":
    from dotenv import load_dotenv
    from tqdm import tqdm

    load_dotenv()

    CONGRESS_API_KEY = os.getenv("CONGRESS_API_KEY")

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
