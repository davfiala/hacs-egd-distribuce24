from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from egd_client_test.api import PRAGUE, ApiError, AuthenticationError, EgdClient


class Response:
    def __init__(self, status, data):
        self.status, self.data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_token_cached_and_authorization_header():
    session = Session(
        [Response(200, {"access_token": "test-token"}), Response(200, []), Response(200, [])]
    )
    client = EgdClient(session, "test-id", "test-secret")
    await client.meters()
    await client.meters()
    assert [c[0] for c in session.calls] == ["POST", "GET", "GET"]
    assert session.calls[1][2]["headers"] == {"Authorization": "Bearer test-token"}
    assert session.calls[0][2]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_expired_token_retried_once():
    session = Session(
        [
            Response(200, {"access_token": "one"}),
            Response(401, {}),
            Response(200, {"access_token": "two"}),
            Response(401, {}),
        ]
    )
    client = EgdClient(session, "id", "secret")
    with pytest.raises(AuthenticationError):
        await client.meters()
    assert len(session.calls) == 4


@pytest.mark.asyncio
async def test_token_renewed_next_day():
    session = Session([Response(200, {"access_token": "new"}), Response(200, [])])
    client = EgdClient(session, "id", "secret")
    client._token = "old"
    client._token_day = datetime.now(PRAGUE).date() - timedelta(days=1)
    await client.meters()
    assert session.calls[0][0] == "POST"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 302])
async def test_errors_do_not_leak_body_or_retry_storm(status):
    session = Session([Response(status, {"secret": "must-not-leak"})])
    client = EgdClient(session, "id", "secret")
    with pytest.raises(ApiError) as error:
        await client.meters()
    assert "must-not-leak" not in str(error.value)
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_chunking_and_exclusive_end():
    client = EgdClient(None, "id", "secret")
    client.get = AsyncMock(return_value=[])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    await client.readings("859182400000000000", "DCQC", start, start + timedelta(days=60))
    assert client.get.await_count == 3
    calls = client.get.await_args_list
    assert calls[0].args[1]["to"] == "2026-01-28T23:59:59.999Z"
    assert calls[1].args[1]["from"] == "2026-01-29T00:00:00.000Z"


@pytest.mark.asyncio
async def test_local_yesterday_boundary():
    client = EgdClient(None, "id", "secret")
    client.get = AsyncMock(return_value=[])
    end = datetime(2026, 9, 21, tzinfo=PRAGUE).astimezone(UTC)
    await client.readings("859182400000000000", "DCQC", end - timedelta(days=1), end)
    assert client.get.await_args.args[1]["to"] == "2026-09-20T21:59:59.999Z"
