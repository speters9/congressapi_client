# tests/test_lists_and_streaming.py
"""
Mocked (offline) tests for list endpoints and iter_entities streaming that were
previously only covered by live-network tests (or not covered at all).
"""
import pytest
import requests_mock

from src.congressapi_client import CongressAPIClient
from src.congressapi_client.models import Amendment, Bill, Vote, VoteMember

API_BASE = "https://api.congress.gov/v3"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", "test_key")
    return CongressAPIClient()


def test_get_bills_list_basic(client, requests_mock):
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [
            {"congress": 118, "type": "HR", "number": "1", "title": "First Bill",
             "latestAction": {"actionDate": "2023-01-05", "text": "Introduced"}},
            {"congress": 118, "type": "HR", "number": "2", "title": "Second Bill",
             "latestAction": {"actionDate": "2023-01-06", "text": "Introduced"}},
        ],
        "pagination": {}
    })

    bills = client.get_bills(congress=118, bill_type="hr")
    assert len(bills) == 2
    assert all(isinstance(b, Bill) for b in bills)
    assert bills[0].title == "First Bill"
    assert bills[1].bill_number == "2"


def test_get_bills_list_limit(client, requests_mock):
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [
            {"congress": 118, "type": "HR", "number": str(n), "title": f"Bill {n}"}
            for n in range(1, 6)
        ],
        "pagination": {}
    })

    bills = client.get_bills(congress=118, bill_type="hr", limit=2)
    assert len(bills) == 2


def test_get_amendments_list(client, requests_mock):
    requests_mock.get(f"{API_BASE}/amendment/118/samdt", json={
        "amendments": [
            {"congress": 118, "type": "SAMDT", "number": "1", "purpose": "To amend",
             "latestAction": {"actionDate": "2023-02-01", "text": "Proposed"}},
        ],
        "pagination": {}
    })

    amendments = client.get_amendments(congress=118, amendment_type="samdt")
    assert len(amendments) == 1
    assert isinstance(amendments[0], Amendment)
    assert amendments[0].amendment_number == "1"
    assert amendments[0].latest_action == "Proposed"


def test_get_votes_list_mocked(client, requests_mock):
    requests_mock.get(f"{API_BASE}/house-vote/118/1", json={
        "votes": [
            {"congress": 118, "session": 1, "rollCallNumber": 10, "date": "2023-03-01",
             "result": "Passed", "yeas": 220, "nays": 200},
        ],
        "pagination": {}
    })

    votes = client.get_votes(chamber="house", congress=118, session=1)
    assert len(votes) == 1
    assert isinstance(votes[0], Vote)
    assert votes[0].vote_number == 10
    assert votes[0].vote_result == "Passed"


def test_get_vote_detail_mocked(client, requests_mock):
    requests_mock.get(f"{API_BASE}/house-vote/118/1/10", json={
        "vote": {"congress": 118, "session": 1, "rollCallNumber": 10,
                  "date": "2023-03-01", "result": "Passed", "yeas": 220, "nays": 200}
    })
    requests_mock.get(f"{API_BASE}/house-vote/118/1/10/members", json={
        "members": [
            {"bioguideId": "A000001", "name": "Alice Smith", "party": "D",
             "state": "CA", "voteCast": "Yea"},
        ]
    })

    vote = client.get_vote("house", 118, 1, 10, include_members=True)
    assert vote.vote_result == "Passed"
    assert len(vote.members) == 1
    assert isinstance(vote.members[0], VoteMember)
    assert vote.members[0].vote_cast == "Yea"


def test_get_vote_members_mocked(client, requests_mock):
    requests_mock.get(f"{API_BASE}/senate-vote/118/1/5/members", json={
        "members": [
            {"bioguideId": "B000002", "name": "Bob Jones", "party": "R",
             "state": "TX", "voteCast": "Nay"},
        ]
    })

    members = client.get_vote_members("senate", 118, 1, 5)
    assert len(members) == 1
    assert members[0].bioguide_id == "B000002"
    assert members[0].vote_cast == "Nay"


def test_get_vote_invalid_chamber(client):
    with pytest.raises(ValueError):
        client.get_votes(chamber="both", congress=118, session=1)


def test_iter_entities_bill_no_hydrate(client, requests_mock):
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [
            {"congress": 118, "type": "HR", "number": "1", "title": "Alpha Bill",
             "policyArea": {"name": "Health"}},
            {"congress": 118, "type": "HR", "number": "2", "title": "Beta Bill",
             "policyArea": {"name": "Education"}},
        ],
        "pagination": {}
    })

    items = list(client.iter_entities(
        entity="bill",
        congress=118,
        bill_type="hr",
    ))
    assert len(items) == 2
    # hydrate=False returns raw dicts, not typed objects
    assert all(isinstance(it, dict) for it in items)


def test_iter_entities_bill_with_where_filter(client, requests_mock):
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [
            {"congress": 118, "type": "HR", "number": "1", "title": "Alpha Bill",
             "policyArea": {"name": "Health"}},
            {"congress": 118, "type": "HR", "number": "2", "title": "Beta Bill",
             "policyArea": {"name": "Education"}},
        ],
        "pagination": {}
    })

    items = list(client.iter_entities(
        entity="bill",
        congress=118,
        bill_type="hr",
        where=lambda b: b.get("policyArea", {}).get("name") == "Health",
    ))
    assert len(items) == 1
    assert items[0]["title"] == "Alpha Bill"


def test_iter_entities_bill_with_hydrate(client, requests_mock):
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [
            {"congress": 118, "type": "HR", "number": "1", "title": "Alpha Bill"},
        ],
        "pagination": {}
    })
    requests_mock.get(f"{API_BASE}/bill/118/hr/1", json={
        "bill": {"congress": 118, "type": "HR", "number": "1", "title": "Alpha Bill",
                  "introducedDate": "2023-01-01"}
    })

    items = list(client.iter_entities(
        entity="bill",
        congress=118,
        bill_type="hr",
        hydrate=True,
    ))
    assert len(items) == 1
    assert isinstance(items[0], Bill)
    assert items[0].title == "Alpha Bill"


def test_iter_entities_congress_range(client, requests_mock):
    requests_mock.get(f"{API_BASE}/bill/117/hr", json={
        "bills": [{"congress": 117, "type": "HR", "number": "1", "title": "Old Bill"}],
        "pagination": {}
    })
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [{"congress": 118, "type": "HR", "number": "1", "title": "New Bill"}],
        "pagination": {}
    })

    items = list(client.iter_entities(
        entity="bill",
        congress_range=(117, 118),
        bill_type="hr",
    ))
    assert len(items) == 2
    assert {it["title"] for it in items} == {"Old Bill", "New Bill"}


def test_iter_entities_member(client, requests_mock):
    requests_mock.get(f"{API_BASE}/member", json={
        "members": [{"bioguideId": "A000001", "firstName": "Alice", "lastName": "Smith"}],
        "pagination": {}
    })

    items = list(client.iter_entities(entity="member"))
    assert len(items) == 1
    assert items[0]["bioguideId"] == "A000001"


def test_iter_entities_hydrate_error_continues(client, requests_mock, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)  # skip real backoff delays
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [
            {"congress": 118, "type": "HR", "number": "1", "title": "Will Fail"},
            {"congress": 118, "type": "HR", "number": "2", "title": "Will Succeed"},
        ],
        "pagination": {}
    })
    requests_mock.get(f"{API_BASE}/bill/118/hr/1", status_code=500)
    requests_mock.get(f"{API_BASE}/bill/118/hr/2", json={
        "bill": {"congress": 118, "type": "HR", "number": "2", "title": "Will Succeed"}
    })

    items = list(client.iter_entities(
        entity="bill",
        congress=118,
        bill_type="hr",
        hydrate=True,
        continue_on_error=True,
    ))
    # The failing bill is skipped; only the successful one is yielded
    assert len(items) == 1
    assert items[0].title == "Will Succeed"


def test_iter_entities_hydrate_error_raises(client, requests_mock, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)  # skip real backoff delays
    requests_mock.get(f"{API_BASE}/bill/118/hr", json={
        "bills": [{"congress": 118, "type": "HR", "number": "1", "title": "Will Fail"}],
        "pagination": {}
    })
    requests_mock.get(f"{API_BASE}/bill/118/hr/1", status_code=500)

    with pytest.raises(Exception):
        list(client.iter_entities(
            entity="bill",
            congress=118,
            bill_type="hr",
            hydrate=True,
            continue_on_error=False,
        ))


def test_iter_entities_requires_congress_for_scoped_entity(client):
    with pytest.raises(ValueError):
        list(client.iter_entities(entity="bill"))
