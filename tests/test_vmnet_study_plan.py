import pytest
import study_plan


def test_rejects_invalid_sizes():
    with pytest.raises(ValueError):
        study_plan.build_study_plan(0, 10)
    with pytest.raises(ValueError):
        study_plan.build_study_plan(3, 0)


def test_three_studies_each_at_least_1000_instances():
    plan = study_plan.build_study_plan()
    assert len(plan) == 3
    for s in plan:
        assert len(s["SOPInstanceUIDs"]) >= 1000


def test_first_study_has_cyrillic_name():
    plan = study_plan.build_study_plan()
    assert plan[0]["PatientName"] == study_plan.CYRILLIC_NAME
    assert any(ord(c) > 127 for c in plan[0]["PatientName"])


def test_uids_are_globally_unique():
    plan = study_plan.build_study_plan(num_studies=3, instances_per_study=50)
    sop = [u for s in plan for u in s["SOPInstanceUIDs"]]
    assert len(sop) == len(set(sop))
    studies = [s["StudyInstanceUID"] for s in plan]
    assert len(studies) == len(set(studies))


def test_is_deterministic():
    assert study_plan.build_study_plan(2, 10) == study_plan.build_study_plan(2, 10)
