"""
Automated Unit Tests for Day 5 - External Tools & Real Domain Data (Health Access Track).
Tests live OpenStreetMap PHC lookup, emergency helplines, tool chaining, data attribution, and graceful failure handling.
"""

import json
import pytest
from health_services import (
    DATA_SOURCE_ATTRIBUTION,
    get_emergency_helpline,
    search_health_facilities,
)
from db import init_db, save_caller_memory, get_caller_memory


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_day5.db"
    init_db(db_file)
    return db_file


def test_phc_lookup_success():
    """Test successful live lookup of Primary Health Centre by district via OpenStreetMap."""
    result = search_health_facilities("jaipur")
    assert result["status"] == "success"
    assert result["count"] >= 1
    facility = result["facilities"][0]
    assert "Jaipur" in facility["name"] or "jaipur" in facility["address"].lower()
    assert facility["emergency_services"] is True


def test_phc_lookup_by_pincode():
    """Test lookup of PHC using 6-digit pincode."""
    result = search_health_facilities("302001")
    assert result["status"] in ("success", "partial_success")


def test_graceful_failure_handling():
    """Test graceful failure path when simulated_failure is True."""
    result = search_health_facilities("jaipur", simulated_failure=True)
    assert result["status"] == "error"
    assert "unreachable" in result["message"].lower() or "timeout" in result["message"].lower()
    assert "108" in result["fallback_recommendation"]


def test_emergency_helplines():
    """Test retrieving government emergency helplines."""
    ambulance = get_emergency_helpline("ambulance")
    assert ambulance["status"] == "success"
    assert ambulance["helpline"]["number"] == "108"

    mental_health = get_emergency_helpline("mental_health")
    assert mental_health["status"] == "success"
    assert "14416" in mental_health["helpline"]["number"]


def test_tool_chaining_memory_location(test_db):
    """Test Day 4 memory location chaining into Day 5 tool lookups."""
    # Step 1: Save user location 'Jaipur' in SQLite memory (Day 4)
    save_caller_memory(
        user_id="test_user_day5",
        name="Harshit",
        facts={"location": "Jaipur", "ongoing_conditions": "Headache"},
        consent_given=True,
        db_path=test_db,
    )

    # Step 2: Retrieve memory and chain location to Day 5 lookup
    record = get_caller_memory("test_user_day5", db_path=test_db)
    assert record is not None
    saved_location = record["facts"].get("location")
    assert saved_location == "Jaipur"

    # Step 3: Run PHC lookup with chained location
    result = search_health_facilities(saved_location)
    assert result["status"] == "success"
    assert "Jaipur" in result["facilities"][0]["name"] or "jaipur" in result["facilities"][0]["address"].lower()
