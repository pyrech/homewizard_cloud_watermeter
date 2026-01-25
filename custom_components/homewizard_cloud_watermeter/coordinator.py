from datetime import timedelta, datetime
import logging

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.const import UnitOfVolume

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class HomeWizardCloudDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api, home_id):
        self.api = api
        self.home_id = home_id
        self._pending_stats = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=60),
        )

    async def _async_update_data(self):
        try:
            devices_data = await self.api.async_get_devices(self.home_id)
            if not devices_data or "errors" in devices_data:
                raise UpdateFailed(f"Error fetching devices: {devices_data.get('errors')}")

            devices = devices_data.get("data", {}).get("home", {}).get("devices", [])
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

        now = dt_util.now()
        date_str = now.strftime("%Y/%m/%d")
        data = []

        # Find watermeter devices and fetch their data
        for device in devices:
            if device.get("type") != "watermeter":
                continue

            _LOGGER.debug("Found watermeter, fetching TSDB data.")

            # Sanitize the identifier for Home Assistant's use
            # This will be used for statistic_id, unique_id, and device_id
            device['sanitized_identifier'] = device["identifier"].replace('/', '_')

            # Retrieve device data
            try:
                tsdb_data = await self.api.async_get_tsdb_data(date_str, self.hass.config.time_zone, device["identifier"])
            except Exception as err:
                raise UpdateFailed(f"Error communicating with API: {err}")

            if not tsdb_data or "values" not in tsdb_data:
                _LOGGER.warning("No TSDB data received for watermeter device.")
                continue

            values = tsdb_data.get("values", [])

            if "recorder" in self.hass.config.components:
                try:
                    await self.async_inject_cleaned_stats(values, device)
                except Exception as err:
                    _LOGGER.error("Failed to inject statistics: %s", err)
            else:
                _LOGGER.debug("Recorder not loaded, skipping statistics injection")

            daily_total = sum(
                float(v.get("water") or 0)
                for v in values
            )

            data.append({
                "daily_total": daily_total,
                "unit": UnitOfVolume.LITERS,
                "device": device,
            })

        return data

    async def async_inject_cleaned_stats(self, values, device):
        """Clean data and inject into Home Assistant statistics."""
        statistic_id = f"{DOMAIN}:{device['sanitized_identifier']}_total"

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True, # Required for the Energy Dashboard
            name=f"{device.get("name")} Total Usage",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfVolume.LITERS,
        )

        hourly_data = {}
        for entry in values:
            if entry.get("water") is None:
                continue

            time = dt_util.parse_datetime(entry["time"])
            hour_timestamp = time.replace(minute=0, second=0, microsecond=0)

            if hour_timestamp not in hourly_data:
                hourly_data[hour_timestamp] = 0.0

            hourly_data[hour_timestamp] += float(entry["water"])

        stat_data = []
        cumulative_sum = 0.0

        for hour in sorted(hourly_data.keys()):
            usage = hourly_data[hour]
            cumulative_sum += usage

            stat_data.append(
                StatisticData(
                    start=hour,
                    state=usage,
                    sum=cumulative_sum
                )
            )

        if stat_data:
            async_add_external_statistics(self.hass, metadata, stat_data)
