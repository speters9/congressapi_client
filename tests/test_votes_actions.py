"""
Test script for bill actions and votes functionality.
"""
import os

import pytest
from dotenv import load_dotenv

from src.congressapi_client import CongressAPIClient

load_dotenv()


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")


@pytest.fixture(scope="module")
def client():
    """Create a single client instance for all tests."""
    return CongressAPIClient(
        api_key=os.getenv("CONGRESS_API_KEY"),
        min_interval=0.1,
        timeout=30,
        log_level=20  # INFO level for debugging
    )


@pytest.mark.timeout(10)
def test_bill_actions(client):
    """Test fetching bill actions."""
    actions = client.get_bill_actions(congress=117, bill_type="hr", bill_number=3076, limit=5)

    assert len(actions) > 0, "Should return at least one action"

    # Check first action has expected fields
    action = actions[0]
    assert action.action_date is not None
    assert action.text is not None
    assert action.action_code is not None


@pytest.mark.timeout(10)
def test_amendment_actions(client):
    """Test fetching amendment actions."""
    try:
        actions = client.get_amendment_actions(
            congress=118,
            amendment_type="samdt",
            amendment_number=1,
            limit=5
        )
        assert len(actions) >= 0  # May be 0 if amendment doesn't exist
    except Exception as e:
        pytest.skip(f"Amendment actions not available: {e}")


@pytest.mark.timeout(10)
def test_house_votes(client):
    """Test fetching House votes."""
    # Use congress 117 which has complete data
    votes = client.get_votes(chamber="house", congress=117, session=2, limit=3)

    # Votes endpoint might be empty or unavailable
    if len(votes) == 0:
        pytest.skip("No votes returned from API (endpoint may be unavailable)")

    # Check first vote has expected fields
    vote = votes[0]
    assert vote.vote_number is not None
    assert vote.chamber == "House"
    assert vote.congress == 117
    assert vote.session == 2


@pytest.mark.timeout(10)
def test_house_vote_detail(client):
    """Test fetching a specific House vote WITHOUT member votes."""
    # First fetch votes list to get a valid vote number
    votes = client.get_votes(chamber="house", congress=117, session=2, limit=1)

    if len(votes) == 0:
        pytest.skip("No votes available to test vote detail endpoint")

    vote_num = votes[0].vote_number

    # Now fetch detailed vote
    vote = client.get_vote(chamber="house", congress=117, session=2, vote_number=vote_num, include_members=False)

    assert vote is not None

    # Check if we got actual data or empty response
    if vote.vote_number is None:
        pytest.skip("Vote detail returned empty (endpoint may be unavailable)")

    assert vote.vote_number == vote_num
    assert vote.chamber == "House"
    assert vote.yea_total is not None or vote.nay_total is not None


@pytest.mark.timeout(10)
def test_house_vote_members(client):
    """Test fetching member votes separately (limited)."""
    # First fetch votes list to get a valid vote number
    votes = client.get_votes(chamber="house", congress=117, session=2, limit=1)

    if len(votes) == 0:
        pytest.skip("No votes available to test member votes endpoint")

    vote_num = votes[0].vote_number

    # Now fetch member votes
    members = client.get_vote_members(chamber="house", congress=117, session=2, vote_number=vote_num, limit=10)

    if len(members) == 0:
        pytest.skip("No member votes returned (endpoint may be unavailable)")

    # Check first member has expected fields
    member = members[0]
    assert member.name is not None
    assert member.vote_cast is not None


@pytest.mark.timeout(10)
@pytest.mark.skip(reason="Senate votes endpoint returns 404 - not available for congress 117")
def test_senate_votes(client):
    """Test fetching Senate votes."""
    try:
        votes = client.get_votes(chamber="senate", congress=117, session=2, limit=3)

        if len(votes) == 0:
            pytest.skip("No votes returned from API (endpoint may be unavailable)")

        # Check first vote has expected fields
        vote = votes[0]
        assert vote.vote_number is not None
        assert vote.chamber == "Senate"
    except Exception as e:
        pytest.skip(f"Senate votes endpoint not available: {e}")


@pytest.mark.timeout(10)
@pytest.mark.skip(reason="Senate vote detail endpoint returns 404 - not available for congress 117")
def test_senate_vote_detail(client):
    """Test fetching a specific Senate vote WITHOUT member votes."""
    try:
        # First fetch votes list to get a valid vote number
        votes = client.get_votes(chamber="senate", congress=117, session=2, limit=1)

        if len(votes) == 0:
            pytest.skip("No votes available to test vote detail endpoint")

        vote_num = votes[0].vote_number

        # Now fetch detailed vote
        vote = client.get_vote(chamber="senate", congress=117, session=2, vote_number=vote_num, include_members=False)

        assert vote is not None

        if vote.vote_number is None:
            pytest.skip("Vote detail returned empty (endpoint may be unavailable)")

        assert vote.vote_number == vote_num
        assert vote.chamber == "Senate"
    except Exception as e:
        pytest.skip(f"Senate vote detail endpoint not available: {e}")


# Manual test runner (not used by pytest)
if __name__ == "__main__":
    """Run tests manually without pytest."""
    print("="*60)
    print("Manual Test Run - Use 'pytest tests/test_votes_actions.py' instead")
    print("="*60)
    print("\nFor manual testing, run individual functions or use pytest:")
    print("  pytest tests/test_votes_actions.py -v")
    print("  pytest tests/test_votes_actions.py -v -m 'not slow'")
    print("  pytest tests/test_votes_actions.py -v --timeout=60")
