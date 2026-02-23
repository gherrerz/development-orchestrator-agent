import pytest

@pytest.mark.django_db
def test_smoke():
    assert 1 + 1 == 2
