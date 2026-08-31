"""Configuratie-flow voor Football Data Multi."""
import voluptuous as vol
import aiohttp
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import selector
from .const import DOMAIN, COMPETITIONS, BASE_URL
import logging

_LOGGER = logging.getLogger(__name__)


class FootballDataFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow voor Football Data Multi."""
    
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Toon configuratiescherm aan gebruiker."""
        errors = {}
        
        if user_input is not None:
            # Valideer API token
            api_token = user_input["api_token"]
            competitions = user_input.get("competitions", ["DED"])
            
            # Test API token
            valid = await self._test_credentials(api_token)
            if not valid:
                errors["api_token"] = "invalid_auth"
            else:
                # Voorkom dubbele entries
                await self.async_set_unique_id(api_token[:8])
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(
                    title="Football Data Multi",
                    data={
                        "api_token": api_token,
                    },
                    options={
                        "competitions": competitions,
                    }
                )

        # Schema voor Home Assistant UI
        schema = vol.Schema({
            vol.Required("api_token"): str,
            vol.Required("competitions", default=["DED"]): selector({
                "select": {
                    "options": [
                        {"value": code, "label": name} 
                        for code, name in COMPETITIONS.items()
                    ],
                    "multiple": True,
                    "mode": "dropdown"
                }
            }),
        })

        return self.async_show_form(
            step_id="user", 
            data_schema=schema, 
            errors=errors,
            description_placeholders={
                "info": "Kies één of meerdere competities om te volgen"
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def _test_credentials(self, api_token):
        """Test of de API token geldig is."""
        try:
            headers = {"X-Auth-Token": api_token}
            timeout = aiohttp.ClientTimeout(total=10)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/competitions",
                    headers=headers,
                    timeout=timeout
                ) as resp:
                    return resp.status == 200
        except Exception as err:
            _LOGGER.error("Fout bij testen API token: %s", err)
            return False


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow voor Football Data Multi.

    (2026-08-31: geen __init__ meer die self.config_entry zelf zet - dat
    is sinds Home Assistant 2025.12 een alleen-lezen property die de
    OptionsFlow-basisclass zelf al invult. Zelf toewijzen gaf een 500-fout
    ("Config flow kon niet geladen worden") zodra je de instellingen van
    deze integratie probeerde te openen. self.config_entry blijft gewoon
    bruikbaar in async_step_init hieronder, alleen niet meer handmatig
    gezet.)
    """

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Update de opties
            return self.async_create_entry(title="", data=user_input)

        # Haal huidige competities op
        current_competitions = self.config_entry.options.get(
            "competitions", 
            self.config_entry.data.get("competitions", ["DED"])
        )

        schema = vol.Schema({
            vol.Required(
                "competitions", 
                default=current_competitions
            ): selector({
                "select": {
                    "options": [
                        {"value": code, "label": name} 
                        for code, name in COMPETITIONS.items()
                    ],
                    "multiple": True,
                    "mode": "dropdown"
                }
            }),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "info": "Pas de competities aan die je wilt volgen"
            }
        )
