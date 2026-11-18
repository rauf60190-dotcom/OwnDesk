from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from rest_framework.exceptions import ValidationError
from apps.core.resources import PolicySerializer
from apps.sla.services import add_work, business_seconds


def schedule():
    return {"week": {str(i): [["09:00", "13:00"], ["14:00", "18:00"]] for i in range(5)},
            "exceptions": {"2026-11-09": []}}


@pytest.mark.parametrize("invalid", [
    {"week": {"0": [["09:00", "18:00"]]}, "exceptions": []},
    {"week": {"0": [["09:00", "18:00"]]}, "exceptions": {"2026-11-09": [["09:00", "13:00"], ["12:00", "18:00"]]}},
    {"week": {"0": [["09:00", "18:00"]]}, "exceptions": {"2026-11-09": None}},
    {"week": {"0": [["18:00", "09:00"]]}},
    {"week": {"0": []}},
    [], None,
])
def test_reject_invalid_schedule(invalid):
    with pytest.raises(ValidationError):
        PolicySerializer().validate_schedule(invalid)


def test_break_weekend_holiday_and_special_hours():
    tz = ZoneInfo("Asia/Tashkent")
    s = PolicySerializer().validate_schedule(schedule())
    friday = datetime(2026, 11, 6, 17, tzinfo=tz)
    deadline = add_work(friday, 7200, s, "Asia/Tashkent")
    assert deadline == datetime(2026, 11, 10, 10, tzinfo=tz)
    assert business_seconds(friday, deadline, s, "Asia/Tashkent") == 7200
    noon = datetime(2026, 11, 6, 12, tzinfo=tz)
    assert add_work(noon, 7200, s, "Asia/Tashkent") == datetime(2026, 11, 6, 15, tzinfo=tz)
    s["exceptions"]["2026-11-07"] = [["10:00", "12:00"]]
    assert add_work(friday, 7200, s, "Asia/Tashkent") == datetime(2026, 11, 7, 11, tzinfo=tz)


def test_midnight_and_always_open():
    assert PolicySerializer().validate_schedule({}) == {}
    s = {"week": {"0": [["00:00", "24:00"]]}}
    assert PolicySerializer().validate_schedule(s) == s
