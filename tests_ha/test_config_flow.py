"""Exercise actual HA forms, including user-selected meters."""

from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType

DOMAIN = "egd_distribuce24"
EAN = "859182400000000000"
OTHER = "859182400000000001"
CLIENT = f"custom_components.{DOMAIN}.config_flow.EgdClient"


async def test_select_one_of_multiple_meters(hass):
    with (
        patch(CLIENT) as client,
        patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True),
    ):
        client.return_value.meters = AsyncMock(
            return_value=[
                {"ean": EAN, "typMereni": "C1"},
                {"ean": OTHER, "typMereni": "B"},
            ]
        )
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "client_id": "test-id",
                "client_secret": "test-secret",
                "include_export": False,
            },
        )
        assert result["step_id"] == "meters"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"meters": [OTHER]}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["meters"] == [OTHER]
        await hass.async_block_till_done()


async def test_empty_selection_stays_in_form(hass):
    with patch(CLIENT) as client:
        client.return_value.meters = AsyncMock(return_value=[{"ean": EAN, "typMereni": "C1"}])
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={
                "client_id": "test-id",
                "client_secret": "test-secret",
            },
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"meters": []})
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "select_meters"}
