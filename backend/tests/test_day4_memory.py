import sys
from pathlib import Path
import pytest

# Add src to python path for testing
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import db
from agent import Assistant


@pytest.fixture
def temp_db(tmp_path):
    test_db_path = tmp_path / "test_health_memory.db"
    db.init_db(test_db_path)
    return test_db_path


def test_init_and_save_caller_memory(temp_db):
    user_id = "user_ramesh"
    name = "Ramesh Kumar"
    language_preference = "Hindi"
    facts = {
        "age_band": "40-50 years",
        "ongoing_conditions": "Frequent headache / Migraine",
        "last_triage_outcome": "Advised hydration, rest, and doctor consultation",
    }

    # Save caller memory with explicit consent
    saved = db.save_caller_memory(
        user_id=user_id,
        name=name,
        language_preference=language_preference,
        facts=facts,
        consent_given=True,
        db_path=temp_db,
    )
    assert saved is True

    # Retrieve caller memory
    record = db.get_caller_memory(name, db_path=temp_db)
    assert record is not None
    assert record["user_id"] == user_id
    assert record["name"] == name
    assert record["language_preference"] == "Hindi"
    assert record["facts"]["ongoing_conditions"] == "Frequent headache / Migraine"
    assert record["consent_given"] is True


def test_consent_refusal_does_not_save(temp_db):
    user_id = "user_priv"
    name = "Anonymous User"

    saved = db.save_caller_memory(
        user_id=user_id,
        name=name,
        facts={"age_band": "20-30"},
        consent_given=False,
        db_path=temp_db,
    )
    assert saved is False

    record = db.get_caller_memory(user_id, db_path=temp_db)
    assert record is None


def test_delete_caller_memory(temp_db):
    user_id = "user_delete"
    name = "Temp User"

    db.save_caller_memory(
        user_id=user_id,
        name=name,
        facts={"ongoing_conditions": "Flu"},
        consent_given=True,
        db_path=temp_db,
    )
    assert db.get_caller_memory(name, db_path=temp_db) is not None

    # Delete memory
    deleted = db.delete_caller_memory(name, db_path=temp_db)
    assert deleted is True

    # Verify deleted
    assert db.get_caller_memory(name, db_path=temp_db) is None


def test_agent_tools_exist():
    agent_inst = Assistant()
    assert hasattr(agent_inst, "lookup_caller")
    assert hasattr(agent_inst, "save_caller_memory")
    assert hasattr(agent_inst, "forget_caller")
