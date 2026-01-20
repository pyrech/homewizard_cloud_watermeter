"""Config flow for HomeWizard Cloud Watermeter integration."""
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import HomeWizardCloudApi
from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_LOCATION_ID

_LOGGER = logging.getLogger(__name__)

class HomeWizardCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomeWizard Cloud Watermeter."""
    VERSION = 1

    def __init__(self):
        """Initialize the flow."""
        self._api = None
        self._data = {}
        self._locations = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step: Login."""
        errors = {}

        if user_input is not None:
            # Get the aiohttp session from Home Assistant
            session = async_get_clientsession(self.hass)
            
            # Initialize our API client with user credentials
            api = HomeWizardCloudApi(
                user_input[CONF_EMAIL], 
                user_input[CONF_PASSWORD], 
                session
            )

            # Validate credentials against the token endpoint
            if await self._api.async_authenticate():
                self._data.update(user_input)
                return await self.async_step_location()
            else:
                # If authentication failed, show an error message
                errors["base"] = "invalid_auth"

        # Form schema for the UI
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }),
            errors=errors,
        )

    async def async_step_location(self, user_input=None):
        """Handle the second step: Select Location."""
        errors = {}
        
        if user_input is not None:
            location_id = user_input[CONF_LOCATION_ID]
            location_name = self._locations[location_id]

            # Use location_id as unique ID to allow multiple instances (one per home)
            await self.async_set_unique_id(str(location_id))
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=location_name,
                data={**self._data, "home_id": location_id}
            )

        # Fetch locations from API
        locations_data = await self._api.async_get_locations()
        if not locations_data:
            return self.async_abort(reason="no_locations")

        # Map ID to Name for the dropdown
        self._locations = {loc["id"]: loc["name"] for loc in locations_data}

        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCATION_ID): vol.In(self._locations),
            }),
            errors=errors,
        )