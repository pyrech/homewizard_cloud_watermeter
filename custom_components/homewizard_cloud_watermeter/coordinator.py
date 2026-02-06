from datetime import timedelta, datetime
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMetaData,
    StatisticMeanType,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics, get_last_statistics, statistics_during_period
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.const import UnitOfVolume

from .const import DOMAIN
from .api import HomeWizardCloudApi

# Period during which we recalculate statistics to catch late cloud data
RECALCULATION_PERIOD = timedelta(hours=48)

_LOGGER = logging.getLogger(__name__)

class HomeWizardCloudDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api: HomeWizardCloudApi, home_id: int):
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
        devices_data = await self.api.async_get_devices(self.home_id)
        if not devices_data:
            raise UpdateFailed(f"Error fetching HomeWizard devices.")

        if "errors" in devices_data:
            raise UpdateFailed(f"Error fetching HomeWizard devices: {devices_data.get('errors')}")

        devices = devices_data.get("data", {}).get("home", {}).get("devices", [])

        now = dt_util.now()
        yesterday = now - timedelta(days=1)

        data = {}

        # Find watermeter devices and fetch their data
        for device in devices:
            if device.get("type") != "watermeter":
                continue

            _LOGGER.debug("Found HomeWizard watermeter device '%s', fetching data.", device["identifier"])

            # Sanitize the identifier for Home Assistant's use
            # This will be used for statistic_id, unique_id, and device_id
            device["sanitized_identifier"] = device["identifier"].replace('/', '_')

            # Retrieve device data
            stats_today = await self.api.async_get_tsdb_data(now, self.hass.config.time_zone, device["identifier"])
            stats_yesterday = await self.api.async_get_tsdb_data(yesterday, self.hass.config.time_zone, device["identifier"])

            if not stats_today or "values" not in stats_today:
                _LOGGER.warning("No data received for watermeter device.")
                continue

            if not stats_yesterday or "values" not in stats_today or "values" not in stats_yesterday:
                _LOGGER.warning("No yesterday data received for watermeter device.")
                continue

            combined_values = stats_yesterday.get("values", []) + stats_today.get("values", [])

            if "recorder" in self.hass.config.components:
                try:
                    await self.async_inject_cleaned_stats(combined_values, device)
                except Exception as err:
                    _LOGGER.error("Failed to inject HomeWizard statistics: %s", err)
            else:
                _LOGGER.debug("Recorder not loaded, skipping HomeWizard statistics injection")

            # Calculate daily total only from non-null values
            today_values = [
                v for v in stats_today.get("values", [])
                if v.get("water") is not None
            ]
            
            # Return None if no data available yet (sensor will show "Unknown")
            # This avoids showing 0 when data simply hasn't arrived from the cloud
            if not today_values:
                daily_total = None
                _LOGGER.debug("No data available yet for today, daily_total = None")
            else:
                daily_total = sum(float(v.get("water") or 0) for v in today_values)
                _LOGGER.debug(
                    "Daily total calculation: %d entries with data, total = %.1f L",
                    len(today_values),
                    daily_total
                )

            last_sync_at = None

            for entry in reversed(combined_values):
                if entry.get("water") is not None:
                    last_sync_at = dt_util.parse_datetime(entry["time"])
                    break
            
            if last_sync_at:
                _LOGGER.debug("Last cloud sync at: %s", last_sync_at.isoformat())

            data[device['sanitized_identifier']] = ({
                "daily_total": daily_total,
                "unit": UnitOfVolume.LITERS,
                "device": device,
                "last_sync_at": last_sync_at,
            })

        return data

    async def async_inject_cleaned_stats(self, values: list, device: dict):
        """Clean data and inject into HA statistics, adding only missing hours.
        
        The cloud sync happens ~4 times per day and data may arrive with delay.
        We check the last 48 hours and only inject hours that are missing.
        """
        statistic_id = f"{DOMAIN}:{device['sanitized_identifier']}_total"
        
        now = dt_util.now()
        check_period_start = now - RECALCULATION_PERIOD
        
        # Get existing statistics for the check period
        existing_stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            check_period_start,
            now,
            {statistic_id},
            "hour",
            None,
            {"state", "sum"}
        )
        
        # Build a map of existing hourly values: hour_utc -> state
        existing_hourly = {}
        if statistic_id in existing_stats and existing_stats[statistic_id]:
            for point in existing_stats[statistic_id]:
                raw_start = point.get("start")
                if raw_start is not None:
                    if isinstance(raw_start, (int, float)):
                        hour_utc = dt_util.utc_from_timestamp(raw_start)
                    else:
                        hour_utc = dt_util.as_utc(raw_start)
                    existing_hourly[hour_utc] = point.get("state") or 0.0
        
        # Get the last known statistics point to continue the sum
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )

        last_sum = 0.0
        last_stat_time = None

        if statistic_id in last_stats and last_stats[statistic_id]:
            point = last_stats[statistic_id][0]
            last_sum = point.get("sum") or 0.0

            raw_start = point.get("start")
            if raw_start is not None:
                if isinstance(raw_start, (int, float)):
                    last_stat_time = dt_util.utc_from_timestamp(raw_start)
                else:
                    last_stat_time = dt_util.as_utc(raw_start)

        metadata = StatisticMetaData(
            has_sum=True,
            name=f"{device.get('name')} Total",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfVolume.LITERS,
            unit_class=SensorDeviceClass.VOLUME,
            mean_type=StatisticMeanType.NONE,
        )

        # Aggregate cloud data by hour
        hourly_data = {}
        for entry in values:
            # Ignore nulls (mainly future hours)
            if entry.get("water") is None:
                continue

            time = dt_util.parse_datetime(entry["time"])
            if not time:
                continue

            hour_timestamp = time.replace(minute=0, second=0, microsecond=0)

            # Security: don't process data far in the future
            if hour_timestamp > dt_util.now() + timedelta(hours=1):
                continue

            if hour_timestamp not in hourly_data:
                hourly_data[hour_timestamp] = 0.0
            hourly_data[hour_timestamp] += float(entry["water"])

        # Find hours that are missing in existing statistics
        hours_to_add = []
        for hour, cloud_value in hourly_data.items():
            hour_utc = dt_util.as_utc(hour)
            
            # Only add if missing from existing statistics
            if hour_utc not in existing_hourly:
                hours_to_add.append(hour)
        
        if not hours_to_add:
            _LOGGER.debug("All statistics are up to date, nothing to inject")
            return
        
        _LOGGER.debug("Found %d missing hours to add", len(hours_to_add))
        
        # We need to recalculate sum from the first missing hour
        first_hour_to_add = min(hours_to_add)
        first_hour_utc = dt_util.as_utc(first_hour_to_add)
        
        # Get the sum just before the first hour to add
        stats_before_first = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            first_hour_utc - timedelta(hours=2),
            first_hour_utc,
            {statistic_id},
            "hour",
            None,
            {"sum"}
        )
        
        base_sum = 0.0
        base_stat_time = None
        
        if statistic_id in stats_before_first and stats_before_first[statistic_id]:
            point = stats_before_first[statistic_id][-1]
            base_sum = point.get("sum") or 0.0
            raw_start = point.get("start")
            if raw_start is not None:
                if isinstance(raw_start, (int, float)):
                    base_stat_time = dt_util.utc_from_timestamp(raw_start)
                else:
                    base_stat_time = dt_util.as_utc(raw_start)

        # Build statistics from first_hour_to_add onwards
        stat_data = []
        cumulative_sum = base_sum
        new_count = 0

        for hour in sorted(hourly_data.keys()):
            hour_utc = dt_util.as_utc(hour)

            # Skip hours before our base reference point
            if base_stat_time and hour_utc <= base_stat_time:
                continue

            usage = hourly_data[hour]
            cumulative_sum += usage
            
            # Only include hours from first_hour_to_add onwards
            if hour >= first_hour_to_add:
                if hour in hours_to_add:
                    new_count += 1
                
                stat_data.append(
                    StatisticData(
                        start=hour,
                        state=usage,
                        sum=cumulative_sum
                    )
                )

        if stat_data:
            _LOGGER.debug(
                "Injecting %d statistics (%d new), final sum=%.1f L",
                len(stat_data), new_count, cumulative_sum
            )
            async_add_external_statistics(self.hass, metadata, stat_data)
