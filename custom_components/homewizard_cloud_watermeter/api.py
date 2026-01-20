import aiohttp
import async_timeout
import logging

_LOGGER = logging.getLogger(__name__)

class HomeWizardCloudApi:
    """ApiClient for HomeWizard Cloud API."""

    def __init__(self, username, password, session: aiohttp.ClientSession):
        self._username = username
        self._password = password
        self._session = session
        self._token = None

    async def async_authenticate(self) -> bool:
        """Authenticate with the Basic Auth to get a Bearer token."""
        url = "https://api.homewizardeasyonline.com/v1/auth/account/token"
        auth = aiohttp.BasicAuth(self._username, self._password)

        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url, auth=auth) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._token = data.get("access_token")
                        _LOGGER.debug("Successfully authenticated. Token received.")
                        return True
                    
                    _LOGGER.error("Authentication failed with status: %s", response.status)
                    return False
        except Exception as ex:
            _LOGGER.error("Error connecting to HomeWizard API: %s", ex)
            return False

    async def async_get_locations(self) -> list:
            """Get the list of locations associated with the account."""
            url = "https://homes.api.homewizard.com/locations"
            headers = await self.get_headers()

            try:
                async with async_timeout.timeout(10):
                    async with self._session.get(url, headers=headers) as response:
                        if response.status == 200:
                            return await response.json()
                        _LOGGER.error("Failed to fetch locations: %s", response.status)
                        return []
            except Exception as ex:
                _LOGGER.error("Error fetching locations: %s", ex)
                return []

    async def get_headers(self):
        """Get headers for GraphQL requests, renewing token if necessary."""
        # Simple implementation: we reuse the token we have.
        # In a full version, we could check expiration here.
        if not self._token:
            await self.async_authenticate()
            
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }