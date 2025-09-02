# tests/test_congressapi.py
import json
import textwrap

import pytest
import requests
import requests_mock

from src.congressapi_client import CongressAPIClient

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
