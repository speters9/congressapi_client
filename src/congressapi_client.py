from __future__ import annotations

import os
import random
import time
import email.utils as eut
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests
from requests import RequestException

from src.models import (
    Bill, BillTextVersion, Committee, CommitteeMeeting, Hearing, HearingFormat,
    Member, MemberRole, Subcommittee
)


class CongressAPI:
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
    ):
        self.api_key = api_key or os.getenv("CONGRESS_API_KEY") or os.getenv("CONGRESS_DOT_GOV_API_KEY")
        if not self.api_key:
            raise ValueError("Congress.gov API key not provided. Set CONGRESS_API_KEY env var or pass api_key=...")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

        # backoff/limits
        self.min_interval = float(min_interval)
        self._last_call_ts = 0.0
        self.max_tries = int(max_tries)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)

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
        p = {"api_key": self.api_key}
        if params:
            p.update({k: v for k, v in params.items() if v is not None})
        resp = self._request_with_backoff("GET", url, params=p)
        return resp.json()

    def _paged(self, first_path: str, data_key: str, params: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
        """
        Iterate list endpoints that return a 'pagination.next' URL and a top-level data_key.
        """
        resp = self._get(first_path, params=params)
        for item in resp.get(data_key, {}).get("item", []) or []:
            yield item
        next_url = resp.get("pagination", {}).get("next")
        while next_url:
            # next_url already includes api_key/offset/limit
            resp2 = self._request_with_backoff("GET", next_url)
            data = resp2.json()
            for item in data.get(data_key, {}).get("item", []) or []:
                yield item
            next_url = data.get("pagination", {}).get("next")

    # ------------- committees -------------
    def get_committees(self, congress: Optional[int] = None, chamber: Optional[str] = None) -> List[Committee]:
        path = "committee" if not (congress and chamber) else f"committee/{congress}/{chamber}"
        items = list(self._paged(path, data_key="committees"))
        out: List[Committee] = []
        for it in items:
            subs = [Subcommittee(system_code=sc.get("systemCode"), name=sc.get("name"))
                    for sc in (it.get("subcommittees", {}) or {}).get("item", []) or []]
            parent = it.get("parent") or {}
            out.append(Committee(
                system_code=it.get("systemCode"),
                name=it.get("name"),
                chamber=it.get("chamber"),
                committee_type=it.get("committeeTypeCode"),
                parent_system_code=parent.get("systemCode"),
                parent_name=parent.get("name"),
                subcommittees=subs,
                raw=it,
            ))
        return out

    def get_committee(self, chamber: str, system_code: str) -> Committee:
        data = self._get(f"committee/{chamber}/{system_code}")
        c = data.get("committee", {})
        subs = [Subcommittee(system_code=sc.get("systemCode"), name=sc.get("name"))
                for sc in (c.get("subcommittees", {}) or {}).get("item", []) or []]
        parent = c.get("parent") or {}
        return Committee(
            system_code=c.get("systemCode"),
            name=c.get("name"),
            chamber=None,
            committee_type=None,
            parent_system_code=parent.get("systemCode"),
            parent_name=parent.get("name"),
            subcommittees=subs,
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
                       for f in (it.get("formats", {}) or {}).get("item", []) or []]
            out.append(Hearing(
                jacket_number=it.get("jacketNumber"),
                title=it.get("title"),
                congress=it.get("congress"),
                chamber=it.get("chamber"),
                citation=it.get("citation"),
                committees=[{"name": x.get("name"), "systemCode": x.get("systemCode")}
                            for x in (it.get("committees", {}) or {}).get("item", []) or []],
                dates=[d.get("date") for d in (it.get("dates", {}) or {}).get("item", []) or []],
                formats=formats,
                raw=it,
            ))
        return out

    def get_hearing(self, congress: int, chamber: str, jacket_number: int) -> Hearing:
        h = self._get(f"hearing/{congress}/{chamber}/{jacket_number}").get("hearing", {})
        formats = [HearingFormat(type=f.get("type"), url=f.get("url"))
                   for f in (h.get("formats", {}) or {}).get("item", []) or []]
        return Hearing(
            jacket_number=h.get("jacketNumber"),
            title=h.get("title"),
            congress=h.get("congress"),
            chamber=h.get("chamber"),
            citation=h.get("citation"),
            committees=[{"name": x.get("name"), "systemCode": x.get("systemCode")}
                        for x in (h.get("committees", {}) or {}).get("item", []) or []],
            dates=[d.get("date") for d in (h.get("dates", {}) or {}).get("item", []) or []],
            formats=formats,
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
                            for x in (it.get("committees", {}) or {}).get("item", []) or []],
                raw=it,
            ))
        return out

    def get_committee_meeting(self, congress: int, chamber: str, event_id: int) -> CommitteeMeeting:
        m = self._get(f"committee-meeting/{congress}/{chamber}/{event_id}").get("committeeMeeting", {})
        return CommitteeMeeting(
            event_id=m.get("eventId"),
            type=m.get("type"),
            title=m.get("title"),
            meeting_status=m.get("meetingStatus"),
            date=m.get("date"),
            chamber=m.get("chamber"),
            committees=[{"name": x.get("name"), "systemCode": x.get("systemCode")}
                        for x in (m.get("committees", {}) or {}).get("item", []) or []],
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
                for r in (it.get("roles", {}) or {}).get("item", []) or []
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
            for r in (m.get("roles", {}) or {}).get("item", []) or []
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
                for tv in (it.get("textVersions", {}) or {}).get("item", []) or []
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
                    raw=it,
                )
            )
        return out

    def get_bill(self, congress: int, bill_type: str, bill_number: int) -> Bill:
        b = self._get(f"bill/{congress}/{bill_type}/{bill_number}").get("bill", {})
        texts = [
            BillTextVersion(type=tv.get("type"), url=tv.get("url"), date=tv.get("date"), raw=tv)
            for tv in (b.get("textVersions", {}) or {}).get("item", []) or []
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
            raw=b,
        )
