# tests/test_congressapi.py
import json
import textwrap

import pytest
import requests
import requests_mock

from src.congressapi_client import CongressAPIClient
from src.congressapi_client.models import Member, MemberTerm, PartyAffiliation

API_BASE = "https://api.congress.gov/v3"

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", "test_key")
    return CongressAPIClient()

def test_url_with_key(client):
    u = client._url_with_key(f"{API_BASE}/hearing/118/house/12345")
    assert "api_key=test_key" in u
    # Non-API assets pass through
    w = client._url_with_key("https://www.congress.gov/118/chrg/foo.pdf")
    assert "api_key" not in w

def test_extract_items(client):
    assert client._extract_items(None) == []
    assert client._extract_items([1,2]) == [1,2]
    assert client._extract_items({"item":[1,2]}) == [1,2]
    assert client._extract_items({"items":[1,2]}) == [1,2]
    # Test single-item cases that might come from XML
    assert client._extract_items({"item": {"id": 1}}) == [{"id": 1}]
    assert client._extract_items({"items": {"id": 2}}) == [{"id": 2}]

def test_committee_meetings_empty_response(client):
    with requests_mock.Mocker() as m:
        m.get(f"{API_BASE}/committee-meeting/118/house", json={
            "committeeMeetings": {},
            "pagination": {}
        })
        meetings = list(client.get_committee_meetings(118, "house"))
        assert len(meetings) == 0

def test_committee_meetings_single_item_xml(client):
    xml_payload = textwrap.dedent("""\
        <root>
          <committeeMeetings>
            <item>
              <eventId>12345</eventId>
              <congress>118</congress>
              <chamber>House</chamber>
              <type>Hearing</type>
              <committees>
                <item>
                  <systemCode>hsju00</systemCode>
                  <name>House Judiciary</name>
                </item>
              </committees>
            </item>
          </committeeMeetings>
          <pagination/>
        </root>
    """)
    with requests_mock.Mocker() as m:
        m.get(f"{API_BASE}/committee-meeting/118/house",
              text=xml_payload,
              headers={"Content-Type":"application/xml"})
        meetings = list(client.get_committee_meetings(118, "house"))
        assert len(meetings) == 1
        assert meetings[0].event_id == "12345"
        assert len(meetings[0].committees) == 1
        assert meetings[0].committees[0]["systemCode"] == "hsju00"

def test_members_mixed_format_pagination(client):
    with requests_mock.Mocker() as m:
        # First page JSON
        m.get(f"{API_BASE}/member", json={
            "members": {"item": [
                {"bioguideId": "A000001", "firstName": "Alice", "lastName": "Smith"}
            ]},
            "pagination": {"next": f"{API_BASE}/member?page=2"}
        })
        # Second page XML with single item
        m.get(f"{API_BASE}/member?page=2&api_key=test_key",
              text=textwrap.dedent("""\
                  <root>
                    <members>
                      <item>
                        <bioguideId>B000002</bioguideId>
                        <firstName>Bob</firstName>
                        <lastName>Jones</lastName>
                      </item>
                    </members>
                    <pagination/>
                  </root>
              """),
              headers={"Content-Type":"application/xml"})
        members = list(client.get_members())
        assert len(members) == 2
        assert members[0].bioguide_id == "A000001"
        assert members[1].bioguide_id == "B000002"

def test_committees_nested_subcommittees(client):
    with requests_mock.Mocker() as m:
        m.get(f"{API_BASE}/committee/house/hsag00", json={
            "committee": {
                "systemCode": "hsag00",
                "name": "House Agriculture",
                "subcommittees": {
                    "item": [
                        {"systemCode": "hsag03", "name": "Subcommittee on Commodity Markets"},
                        {"systemCode": "hsag05", "name": "Subcommittee on Conservation"}
                    ]
                }
            }
        })
        committee = client.get_committee("house", "hsag00")
        assert committee.system_code == "hsag00"
        assert len(committee.subcommittees) == 2
        assert committee.subcommittees[0].system_code == "hsag03"

def test_get_hearings_json_then_detail_json(client):
    with requests_mock.Mocker() as m:
        # list
        m.get(f"{API_BASE}/hearing/118/house", json={
            "hearings": {"item": [
                {"jacketNumber": 111, "congress":118, "chamber":"House", "url": f"{API_BASE}/hearing/118/house/111"},
            ]},
            "pagination": {}
        })
        # detail
        m.get(f"{API_BASE}/hearing/118/house/111", json={
            "hearing": {
                "jacketNumber":111, "congress":118, "chamber":"House", "title":"Test",
                "committees":{"item":[{"name":"House Armed Services Committee","systemCode":"hsas00"}]},
                "formats":{"item":[{"type":"PDF","url":"https://congress.gov/pdf111.pdf"}]}
            }
        })

        thin = client.get_hearings(118, "house")
        assert len(thin) == 1
        full = client.get_hearing(118, "house", 111)
        assert full.title == "Test"
        assert full.formats[0].type == "PDF"

def test_pagination_and_retry_after_and_xml(client, monkeypatch):
    # Simulate: first page JSON with next; second page XML
    xml_payload = textwrap.dedent("""\
        <root>
            <hearings>
            <item>
                <jacketNumber>222</jacketNumber>
                <congress>118</congress>
                <chamber>House</chamber>
                <url>https://api.congress.gov/v3/hearing/118/house/222</url>
            </item>
            </hearings>
            <pagination/>
        </root>
    """)
    with requests_mock.Mocker() as m:
        print("[TEST] Setting up mock responses...")
        # need xmltodict if your client uses it — install in test env
        # first call (list, JSON) with a next link that lacks api_key
        m.get(f"{API_BASE}/hearing/118", json={
            "hearings":{"item":[
                {"jacketNumber":111,"congress":118,"chamber":"House","url":f"{API_BASE}/hearing/118/house/111"}
            ]},
            "pagination":{"next": f"{API_BASE}/hearing/118?page=2"}
        })
        print("[TEST] First page mock set up (JSON)")

        # the "next" page returns XML
        m.get(f"{API_BASE}/hearing/118?page=2&api_key=test_key",
                text=xml_payload,
                headers={"Content-Type":"application/xml"})
        print("[TEST] Second page mock set up (XML)")

        print("[TEST] Starting client.get_hearings(118)...")
        items = []
        for item in client.get_hearings(118):
            print(f"[TEST] Received item: jacket_number={item.jacket_number}, raw={item.raw}")
            items.append(item)
        print(f"[TEST] Collected {len(items)} items")
        print("[TEST] Final items:", [{"jacket_number": h.jacket_number, "raw": h.raw} for h in items])

        assert any(h.jacket_number == 111 for h in items)
        assert any(h.jacket_number == 222 for h in items)

def test_backoff_on_429(client, monkeypatch):
    calls = {"n": 0}
    def _mock_request(method, url, params=None, timeout=None):
        calls["n"] += 1
        resp = requests.Response()
        if calls["n"] == 1:
            resp.status_code = 429
            resp.headers["Retry-After"] = "0.1"
            resp._content = b"rate"
            resp.url = url
            return resp
        resp.status_code = 200
        resp._content = json.dumps({"hearings":{"item":[]}, "pagination":{}}).encode()
        resp.url = url
        return resp

    client.session.request = _mock_request  # monkey-patch
    out = client.get_hearings(118)  # should succeed on 2nd try
    assert isinstance(out, list)

def test_committee_meeting_detail_rich(client, requests_mock):
    url = "https://api.congress.gov/v3/committee-meeting/118/house/115281"
    requests_mock.get(url, json={
        "committeeMeeting": {
            "eventId": 115281,
            "type": "Hearing",
            "title": "Budget Request",
            "meetingStatus": "Held",
            "date": "2024-03-15",
            "chamber": "House",
            "committees": {"item":[{"name":"House Armed Services Committee","systemCode":"hsas00"}]},
            "witnesses": {"item":[{"name":"GEN John Q. Public"}]},
            "meetingDocuments": {"item":[{"type":"Memo","url":"https://example/memo.pdf"}]},
            "videos": {"item":[{"url":"https://example/video"}]},
            "bills": {"item":[{"congress":118,"type":"hr","number":1234}]},
            "nominations": {"item":[{"congress":118,"number":567}]},
            "treaties": {"item":[{"congress":118,"number":3}]},
            "url": url
        }
    })
    cm = client.get_committee_meeting(118, "house", 115281)
    assert cm.witnesses and cm.documents and cm.videos
    assert cm.related_bills and cm.related_nominations and cm.related_treaties

def test_bill_cosponsors(client, requests_mock):
    # Test getting bill detail with cosponsorship summary
    bill_url = f"{API_BASE}/bill/117/hr/3076"
    requests_mock.get(bill_url, json={
        "bill": {
            "congress": 117,
            "type": "HR",
            "number": "3076",
            "title": "Postal Service Reform Act of 2022",
            "introducedDate": "2021-05-11",
            "originChamber": "House",
            "originChamberCode": "H",
            "latestAction": {
                "actionDate": "2022-04-06",
                "text": "Became Public Law No: 117-108."
            },
            "policyArea": {
                "name": "Government Operations and Politics"
            },
            "sponsor": {"bioguideId": "M000087", "firstName": "CAROLYN", "lastName": "MALONEY"},
            "sponsors": [{"bioguideId": "M000087", "firstName": "CAROLYN", "lastName": "MALONEY"}],
            "laws": [{"number": "117-108", "type": "Public Law"}],
            "cosponsors": {
                "count": 102,
                "countIncludingWithdrawnCosponsors": 102,
                "url": f"{API_BASE}/bill/117/hr/3076/cosponsors"
            },
            "actions": {
                "count": 20,
                "url": f"{API_BASE}/bill/117/hr/3076/actions"
            },
            "relatedBills": {
                "count": 4,
                "url": f"{API_BASE}/bill/117/hr/3076/relatedbills"
            },
            "subjects": {
                "count": 17,
                "url": f"{API_BASE}/bill/117/hr/3076/subjects"
            },
            "legislationUrl": "https://congress.gov/bill/117th-congress/house-bill/3076",
            "updateDate": "2022-09-29T03:27:05Z",
            "textVersions": {"item": []},
            "url": bill_url
        }
    })

    # Test getting full cosponsors list
    cosponsors_url = f"{API_BASE}/bill/117/hr/3076/cosponsors"
    requests_mock.get(cosponsors_url, json={
            "cosponsors": {"item": [
                {
                    "bioguideId": "S000185",
                    "firstName": "Robert",
                    "lastName": "Scott",
                    "fullName": "Rep. Scott, Robert C. [D-VA-3]",
                    "party": "D",
                    "state": "VA",
                    "district": "3",
                    "sponsorshipDate": "2021-05-11",
                    "isOriginalCosponsor": True,
                    "url": f"{API_BASE}/member/S000185"
                },
                {
                    "bioguideId": "J000032",
                    "firstName": "Sheila",
                    "lastName": "Jackson Lee",
                    "fullName": "Rep. Jackson Lee, Sheila [D-TX-18]",
                    "party": "D",
                    "state": "TX",
                    "district": "18",
                    "sponsorshipDate": "2021-05-15",
                    "sponsorshipWithdrawnDate": "2021-06-01",
                    "isOriginalCosponsor": False,
                    "url": f"{API_BASE}/member/J000032"
                }
            ]}
        })

    # Mock subjects endpoint for hydrate=True test
    subjects_url = f"{API_BASE}/bill/117/hr/3076/subjects"
    requests_mock.get(subjects_url, json={
        "subjects": {
            "legislativeSubjects": [
                {"name": "Government employee pay, benefits, personnel management", "updateDate": "2022-02-18T16:38:41Z"},
                {"name": "Postal service", "updateDate": "2022-02-18T16:38:41Z"}
            ]
        }
    })

    # Test bill detail without cosponsors
    bill = client.get_bill(117, "hr", 3076)
    assert bill.cosponsors_count == 102
    assert bill.cosponsors_count_including_withdrawn == 102
    assert bill.cosponsors_url is not None
    assert len(bill.cosponsors) == 0  # Not fetched by default

    # Test new fields
    assert bill.introduced_date == "2021-05-11"
    assert bill.origin_chamber == "House"
    assert bill.origin_chamber_code == "H"
    assert bill.latest_action == "Became Public Law No: 117-108."
    assert bill.latest_action_date == "2022-04-06"
    assert bill.policy_area == "Government Operations and Politics"
    assert len(bill.laws) == 1
    assert bill.laws[0]["number"] == "117-108"
    assert bill.actions_count == 20
    assert bill.related_bills_count == 4
    assert bill.subjects_count == 17
    assert bill.legislation_url == "https://congress.gov/bill/117th-congress/house-bill/3076"
    assert bill.update_date == "2022-09-29T03:27:05Z"

    # Test sponsors consolidation - should merge sponsor and sponsors fields into Member objects
    assert len(bill.sponsors) == 1  # Should consolidate sponsor + sponsors fields
    sponsor = bill.sponsors[0]
    assert isinstance(sponsor, Member)  # Should be Member object
    assert sponsor.bioguide_id == "M000087"
    assert sponsor.first_name == "CAROLYN"
    assert sponsor.last_name == "MALONEY"

    # Test bill detail with cosponsors
    bill_with_cosponsors = client.get_bill(117, "hr", 3076, hydrate=True)
    assert len(bill_with_cosponsors.cosponsors) == 2

    # Check first cosponsor
    cosponsor1 = bill_with_cosponsors.cosponsors[0]
    assert isinstance(cosponsor1, Member)  # Should be Member object
    assert cosponsor1.bioguide_id == "S000185"
    assert cosponsor1.full_name == "Rep. Scott, Robert C. [D-VA-3]"
    assert cosponsor1.party == "D"
    assert cosponsor1.is_original_cosponsor is True
    assert cosponsor1.sponsorship_withdrawn_date is None

    # Check second cosponsor (withdrawn)
    cosponsor2 = bill_with_cosponsors.cosponsors[1]
    assert cosponsor2.bioguide_id == "J000032"
    assert cosponsor2.sponsorship_withdrawn_date == "2021-06-01"
    assert cosponsor2.is_original_cosponsor is False

    # Test getting cosponsors directly
    cosponsors = client.get_bill_cosponsors(117, "hr", 3076)
    assert len(cosponsors) == 2
    assert all(c.bioguide_id for c in cosponsors)

def test_amendment_cosponsors(client, requests_mock):
    # Test getting amendment detail with cosponsorship summary
    amendment_url = f"{API_BASE}/amendment/118/samdt/1123"
    requests_mock.get(amendment_url, json={
        "amendment": {
            "congress": 118,
            "type": "samdt",
            "number": 1123,
            "description": "An amendment to improve the bill",
            "purpose": "To enhance provisions relating to congressional oversight",
            "chamber": "Senate",
            "proposedDate": "2023-07-15",
            "submittedDate": "2023-07-16",
            "amendedBill": {
                "congress": 118,
                "type": "hr",
                "number": "815",
                "title": "Lower Energy Costs Act"
            },
            "latestAction": {
                "actionDate": "2023-07-20",
                "text": "Amendment SA 1123 agreed to in Senate by Voice Vote."
            },
            "sponsors": [{"bioguideId": "C000880", "firstName": "Mike", "lastName": "Crapo"}],
            "cosponsors": {
                "count": 5,
                "countIncludingWithdrawnCosponsors": 6,
                "url": f"{API_BASE}/amendment/118/samdt/1123/cosponsors"
            },
            "actions": {
                "count": 3,
                "url": f"{API_BASE}/amendment/118/samdt/1123/actions"
            },
            "textVersions": {
                "count": 1,
                "url": f"{API_BASE}/amendment/118/samdt/1123/text"
            },
            "updateDate": "2023-07-21T10:30:00Z",
            "url": amendment_url
        }
    })

    # Test getting full amendment cosponsors list
    cosponsors_url = f"{API_BASE}/amendment/118/samdt/1123/cosponsors"
    requests_mock.get(cosponsors_url, json={
        "cosponsors": {"item": [
            {
                "bioguideId": "T000250",
                "firstName": "John",
                "lastName": "Thune",
                "fullName": "Sen. Thune, John [R-SD]",
                "party": "R",
                "state": "SD",
                "sponsorshipDate": "2023-07-15",
                "isOriginalCosponsor": True,
                "url": f"{API_BASE}/member/T000250"
            },
            {
                "bioguideId": "B000575",
                "firstName": "Roy",
                "lastName": "Blunt",
                "fullName": "Sen. Blunt, Roy [R-MO]",
                "party": "R",
                "state": "MO",
                "sponsorshipDate": "2023-07-16",
                "sponsorshipWithdrawnDate": "2023-07-18",
                "isOriginalCosponsor": False,
                "url": f"{API_BASE}/member/B000575"
            }
        ]}
    })

    # Test amendment detail without cosponsors (no hydration)
    amendment = client.get_amendment(118, "samdt", 1123)
    assert amendment.congress == 118
    assert amendment.amendment_type == "samdt"
    assert amendment.amendment_number == 1123
    assert amendment.description == "An amendment to improve the bill"
    assert amendment.chamber == "Senate"
    assert amendment.proposed_date == "2023-07-15"
    assert amendment.submitted_date == "2023-07-16"
    assert amendment.amended_bill["type"] == "hr"
    assert amendment.amended_bill["number"] == "815"
    assert amendment.cosponsors_count == 5
    assert amendment.cosponsors_count_including_withdrawn == 6
    assert amendment.text_count == 1
    assert len(amendment.cosponsors) == 0  # Not fetched by default

    # Test amendment sponsors are converted to Member objects
    assert len(amendment.sponsors) == 1
    sponsor = amendment.sponsors[0]
    assert isinstance(sponsor, Member)  # Should be Member object
    assert sponsor.bioguide_id == "C000880"
    assert sponsor.first_name == "Mike"
    assert sponsor.last_name == "Crapo"

    # Test amendment detail with cosponsors (with hydration)
    amendment_with_cosponsors = client.get_amendment(118, "samdt", 1123, hydrate=True)
    assert len(amendment_with_cosponsors.cosponsors) == 2

    # Check first cosponsor
    cosponsor1 = amendment_with_cosponsors.cosponsors[0]
    assert isinstance(cosponsor1, Member)  # Should be Member object
    assert cosponsor1.bioguide_id == "T000250"
    assert cosponsor1.full_name == "Sen. Thune, John [R-SD]"
    assert cosponsor1.party == "R"
    assert cosponsor1.state == "SD"
    assert cosponsor1.is_original_cosponsor is True
    assert cosponsor1.sponsorship_withdrawn_date is None

    # Check second cosponsor (withdrawn)
    cosponsor2 = amendment_with_cosponsors.cosponsors[1]
    assert cosponsor2.bioguide_id == "B000575"
    assert cosponsor2.sponsorship_withdrawn_date == "2023-07-18"
    assert cosponsor2.is_original_cosponsor is False

    # Test getting amendment cosponsors directly
    cosponsors = client.get_amendment_cosponsors(118, "samdt", 1123)
    assert len(cosponsors) == 2
    assert all(c.bioguide_id for c in cosponsors)

def test_bill_amendments_with_cosponsors(client, requests_mock):
    # Test getting bill amendments with full cosponsor hydration
    bill_amendments_url = f"{API_BASE}/bill/118/hr/815/amendments"
    requests_mock.get(bill_amendments_url, json={
        "amendments": {"item": [
            {
                "congress": 118,
                "type": "samdt",
                "number": 1123,
                "description": "An amendment to improve the bill",
                "purpose": "To enhance provisions",
                "latestAction": {
                    "actionDate": "2023-07-20",
                    "text": "Amendment agreed to"
                },
                "updateDate": "2023-07-21T10:30:00Z",
                "url": f"{API_BASE}/amendment/118/samdt/1123"
            }
        ]}
    })

    # Mock the individual amendment detail call (used during hydration)
    amendment_detail_url = f"{API_BASE}/amendment/118/samdt/1123"
    requests_mock.get(amendment_detail_url, json={
        "amendment": {
            "congress": 118,
            "type": "samdt",
            "number": 1123,
            "description": "An amendment to improve the bill",
            "purpose": "To enhance provisions",
            "chamber": "Senate",
            "proposedDate": "2023-07-15",
            "submittedDate": "2023-07-16",
            "amendedBill": {
                "congress": 118,
                "type": "hr",
                "number": "815"
            },
            "latestAction": {
                "actionDate": "2023-07-20",
                "text": "Amendment agreed to"
            },
            "sponsors": [{"bioguideId": "C000880", "firstName": "Mike", "lastName": "Crapo"}],
            "cosponsors": {
                "count": 2,
                "url": f"{API_BASE}/amendment/118/samdt/1123/cosponsors"
            },
            "updateDate": "2023-07-21T10:30:00Z",
            "url": amendment_detail_url
        }
    })

    # Mock the amendment cosponsors call
    amendment_cosponsors_url = f"{API_BASE}/amendment/118/samdt/1123/cosponsors"
    requests_mock.get(amendment_cosponsors_url, json={
        "cosponsors": {"item": [
            {
                "bioguideId": "T000250",
                "firstName": "John",
                "lastName": "Thune",
                "fullName": "Sen. Thune, John [R-SD]",
                "party": "R",
                "state": "SD",
                "sponsorshipDate": "2023-07-15",
                "isOriginalCosponsor": True
            }
        ]}
    })

    # Test bill amendments without hydration (summary data only)
    amendments = client.get_bill_amendments(118, "hr", 815)
    assert len(amendments) == 1
    amendment = amendments[0]
    assert amendment.amendment_type == "samdt"
    assert amendment.amendment_number == 1123
    assert len(amendment.cosponsors) == 0  # No cosponsors in summary
    assert amendment.chamber is None  # No chamber in summary

    # Test bill amendments with hydration (full details including cosponsors)
    amendments_hydrated = client.get_bill_amendments(118, "hr", 815, hydrate=True)
    assert len(amendments_hydrated) == 1
    amendment_hydrated = amendments_hydrated[0]
    assert amendment_hydrated.amendment_type == "samdt"
    assert amendment_hydrated.amendment_number == 1123
    assert amendment_hydrated.chamber == "Senate"  # Available with hydration
    assert amendment_hydrated.proposed_date == "2023-07-15"  # Available with hydration
    assert len(amendment_hydrated.cosponsors) == 1  # Cosponsors fetched with hydration

    cosponsor = amendment_hydrated.cosponsors[0]
    assert cosponsor.bioguide_id == "T000250"
    assert cosponsor.full_name == "Sen. Thune, John [R-SD]"

def test_bill_subjects(client, requests_mock):
    # Test getting bill detail with subjects summary
    bill_url = f"{API_BASE}/bill/117/hr/7939"
    requests_mock.get(bill_url, json={
        "bill": {
            "congress": 117,
            "type": "HR",
            "number": "7939",
            "title": "Student Veteran Work Study Modernization Act",
            "introducedDate": "2022-05-31",
            "originChamber": "House",
            "latestAction": {
                "actionDate": "2022-06-08",
                "text": "Referred to the House Committee on Veterans' Affairs."
            },
            "subjects": {
                "count": 11,
                "url": f"{API_BASE}/bill/117/hr/7939/subjects"
            },
            "textVersions": {"item": []},
            "url": bill_url
        }
    })

    # Test getting full subjects list
    subjects_url = f"{API_BASE}/bill/117/hr/7939/subjects"
    requests_mock.get(subjects_url, json={
        "pagination": {"count": 11},
        "request": {
            "billNumber": "7939",
            "billType": "hr",
            "billUrl": "https://api.congress.gov/v3/bill/117/hr/7939?format=json",
            "congress": "117",
            "contentType": "application/json",
            "format": "json"
        },
        "subjects": {
            "legislativeSubjects": [
                {"name": "Educational facilities and institutions", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Educational technology and distance education", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Employment and training programs", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Higher education", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Long-term, rehabilitative, and terminal care", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Student aid and college costs", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Temporary and part-time employment", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Unemployment", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Veterans' education, employment, rehabilitation", "updateDate": "2022-06-09T15:30:34Z"},
                {"name": "Veterans' loans, housing, homeless programs", "updateDate": "2022-06-09T15:30:34Z"}
            ],
            "policyArea": {
                "name": "Armed Forces and National Security",
                "updateDate": "2022-06-08T18:00:36Z"
            }
        }
    })

    # Test bill detail without subjects
    bill = client.get_bill(117, "hr", 7939)
    assert bill.subjects_count == 11
    assert bill.subjects_url is not None
    assert len(bill.subjects) == 0  # Not fetched by default

    # Test bill detail with subjects (hydrate=True)
    bill_with_subjects = client.get_bill(117, "hr", 7939, hydrate=True)
    assert len(bill_with_subjects.subjects) == 10  # Should have the subjects

    # Check that subjects are strings (just names)
    assert all(isinstance(s, str) for s in bill_with_subjects.subjects)
    assert "Educational facilities and institutions" in bill_with_subjects.subjects
    assert "Higher education" in bill_with_subjects.subjects
    assert "Veterans' education, employment, rehabilitation" in bill_with_subjects.subjects

    # Test getting subjects directly
    subjects = client.get_bill_subjects(117, "hr", 7939)
    assert len(subjects) == 10
    assert all(isinstance(s, str) for s in subjects)
    assert subjects[0] == "Educational facilities and institutions"
