"""Football Data Multi custom component voor Home Assistant."""
import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .coordinator import FootballDataCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Setup van Football Data Multi via config entry."""
    api_token = entry.data.get("api_token")
    
    # Competitions komen uit options (als die er zijn) of uit data (oude installaties)
    competitions = entry.options.get("competitions", entry.data.get("competitions", ["DED"]))
    
    if not api_token:
        _LOGGER.error("Geen API token gevonden voor Football Data Multi")
        return False

    _LOGGER.info(f"Setting up Football Data Multi voor competities: {competitions}")

    # Maak coordinator aan
    coordinator = FootballDataCoordinator(
        hass,
        api_token,
        competitions,
        update_interval=1800  # 30 minuten
    )

    # Sla coordinator op in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward naar sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    # Luister naar options updates
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # (2026-08-31) GEEN await coordinator.async_config_entry_first_refresh()
    # meer hier - dat blokkeerde het opstarten van de integratie totdat ALLE
    # competities zijn opgehaald, en met de rate-limit-pauzes (6,5s na elke
    # van de 21 aanroepen, ~2+ minuten totaal) duurde dat langer dan de tijd
    # die Home Assistant een integratie geeft om op te starten, met een
    # CancelledError als gevolg ("Instellen mislukt"). De sensoren bestaan nu
    # meteen (tonen "niet beschikbaar" totdat er data is, zie coordinator.py).
    #
    # (2026-08-31, 2e poging) hass.async_create_task() bleek NIET voldoende:
    # die taken worden door Home Assistant nog steeds afgewacht bij een volle
    # herstart (async_block_till_done() tijdens het opstartproces), waardoor
    # het "Home Assistant wordt gestart"-scherm alsnog >2 minuten bleef
    # hangen. entry.async_create_background_task() is de door Home Assistant
    # bedoelde manier om een taak te starten die het opstart-/afsluitproces
    # bewust NIET afwacht.
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        f"football_data_multi_eerste_refresh_{entry.entry_id}",
    )

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    _LOGGER.info("Options gewijzigd, herladen van integratie...")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
