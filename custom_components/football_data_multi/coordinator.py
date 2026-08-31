"""Football Data Multi Coordinator voor Home Assistant."""
import logging
from datetime import datetime, timedelta, timezone
import asyncio
import aiohttp

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)

# (2026-08-31) De gratis laag van football-data.org staat maximaal 10
# requests per minuut toe. Deze coordinator doet 3 aanroepen per
# competitie (standings, live, scheduled); zonder pauze ertussen liep dit
# bij >=4 competities al tegen de limiet aan, waardoor latere competities
# in dezelfde update-cyclus een lege/foutieve response terugkregen
# (zichtbaar als "next_match is NONE or EMPTY" in het logboek, ook al
# bestond de wedstrijd wel degelijk). 6.5 seconden pauze na elke aanroep
# houdt het gemiddelde ruim onder de 10/minuut, ook bij alle 7 competities
# tegelijk (21 aanroepen x 6.5s ~= 137s, ruim binnen de standaard
# update_interval van 300s).
REQUEST_DELAY_SECONDS = 6.5

# (2026-08-31) football-data.org's "status=LIVE" filter is een shorthand
# voor IN_PLAY/PAUSED. Op de gratis laag komt het voor dat een wedstrijd
# die allang is afgelopen serverside niet (op tijd) wordt omgezet naar
# FINISHED, waardoor hij in deze filter oneindig als "Live"/"Rust" is
# blijven staan (bijv. een wedstrijd van gisteravond die vandaag nog als
# lopend werd getoond). Een normale voetbalwedstrijd (incl. rust en
# blessuretijd) duurt nooit langer dan zo'n 2,5 uur; MAX_LIVE_MATCH_AGE
# geeft nog wat extra marge en wordt gebruikt om zulke verouderde entries
# er client-side uit te filteren, ongeacht wat de API zelf nog als status
# doorgeeft.
MAX_LIVE_MATCH_AGE = timedelta(hours=3, minutes=30)

class FootballDataCoordinator(DataUpdateCoordinator):
    """Haalt data op van meerdere Football-Data.org competities."""

    def __init__(self, hass, api_token, competitions, update_interval=300):
        super().__init__(
            hass,
            _LOGGER,
            name="Football Data Multi Coordinator",
            update_interval=timedelta(seconds=update_interval),
        )
        self._headers = {"X-Auth-Token": api_token}
        self._competitions = competitions
        # (2026-08-31) Begin met een lege dict i.p.v. de DataUpdateCoordinator-
        # default None. Sinds de opstart niet meer wacht op de eerste (trage,
        # met opzet vertraagde) refresh - zie __init__.py - bestaan de
        # sensor-entiteiten al vóórdat er ooit data is opgehaald. Hun
        # `available`-property doet `self.code in self.coordinator.data`,
        # wat crasht op None maar prima werkt op een lege dict (gewoon False,
        # entiteit toont "niet beschikbaar" totdat de eerste refresh klaar is).
        self.data = {}

    async def _get_json(self, session, url):
        """Haalt JSON op met foutafhandeling."""
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with session.get(url, headers=self._headers, timeout=timeout) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise UpdateFailed(f"API-fout {resp.status}: {text}")
                return await resp.json()
        except asyncio.TimeoutError:
            raise UpdateFailed(f"Timeout bij ophalen van {url}")
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Client error bij ophalen van {url}: {err}")

    async def _safe_get(self, session, url):
        """Helper die fouten opvangt maar de coordinator laat doorlopen."""
        try:
            return await self._get_json(session, url)
        except UpdateFailed as err:
            _LOGGER.warning("Kon data niet ophalen van %s: %s", url, err)
            return {}
        finally:
            # Pauze na ELKE aanroep (geslaagd of niet) om onder de
            # rate-limit van de gratis API-laag te blijven - zie
            # REQUEST_DELAY_SECONDS hierboven.
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    @staticmethod
    def _filter_stale_live_matches(code, raw_matches):
        """Filtert wedstrijden eruit die te oud zijn om nog echt live te kunnen zijn.

        Zie MAX_LIVE_MATCH_AGE hierboven voor de reden: de API zelf zet een
        wedstrijd soms niet op tijd naar FINISHED, dus we vertrouwen niet
        blind op de status=LIVE-filter van football-data.org.
        """
        now = datetime.now(timezone.utc)
        filtered = []
        for m in raw_matches:
            utc_date_str = m.get("utcDate")
            kickoff = None
            if utc_date_str:
                try:
                    kickoff = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
                except ValueError:
                    kickoff = None

            if kickoff is None:
                # Kan de datum niet parsen: liever tonen dan onterecht wegfilteren.
                filtered.append(m)
                continue

            age = now - kickoff
            if age <= MAX_LIVE_MATCH_AGE:
                filtered.append(m)
            else:
                _LOGGER.warning(
                    "Live-wedstrijd %s - %s (aftrap %s, status %s) genegeerd voor "
                    "competitie %s: %s oud, waarschijnlijk niet tijdig door de API "
                    "op FINISHED gezet.",
                    m.get("homeTeam", {}).get("name"),
                    m.get("awayTeam", {}).get("name"),
                    utc_date_str,
                    m.get("status"),
                    code,
                    age,
                )
        return filtered

    async def _fetch_competition_data(self, session, code):
        """Haalt data op voor één competitie."""
        _LOGGER.info("=== Ophalen data voor competitie %s ===", code)

        standings = await self._safe_get(session, f"{BASE_URL}/competitions/{code}/standings")
        live = await self._safe_get(session, f"{BASE_URL}/competitions/{code}/matches?status=LIVE")
        live_matches = self._filter_stale_live_matches(code, live.get("matches", []))

        # Gebruik /competitions/{code}/matches zoals in het werkende testscript
        scheduled = await self._safe_get(session, f"{BASE_URL}/competitions/{code}/matches?status=SCHEDULED")

        matches_count = len(scheduled.get('matches', []))
        _LOGGER.info("Scheduled voor %s: %s wedstrijden gevonden", code, matches_count)
        
        if matches_count > 0:
            first_match = scheduled.get("matches", [])[0]
            _LOGGER.info(
                "Eerste match (ongesorteerd): %s - %s op %s",
                first_match.get("homeTeam", {}).get("name"),
                first_match.get("awayTeam", {}).get("name"),
                first_match.get("utcDate")
            )

        total_stand = next(
            (s for s in standings.get("standings", []) if s.get("type") == "TOTAL"),
            {},
        )

        matches = scheduled.get("matches", [])
        matches.sort(key=lambda x: x.get("utcDate", ""))
        next_match = matches[0] if matches else None

        if next_match:
            _LOGGER.warning(
                "=== Next match voor %s: %s - %s op %s ===",
                code,
                next_match.get('homeTeam', {}).get('name'),
                next_match.get('awayTeam', {}).get('name'),
                next_match.get('utcDate')
            )
        else:
            _LOGGER.error("=== GEEN next_match voor %s! ===", code)

        result = {
            "standings": total_stand.get("table", []),
            "live_matches": live_matches,
            "next_match": next_match,
        }
        
        _LOGGER.warning("Result voor %s: keys=%s, next_match is None=%s", code, list(result.keys()), result["next_match"] is None)
        
        return result

    async def _async_update_data(self):
        """Haalt alle competities op."""
        data = {}
        async with aiohttp.ClientSession() as session:
            for code in self._competitions:
                try:
                    data[code] = await self._fetch_competition_data(session, code)
                except Exception as err:
                    _LOGGER.error("Fout bij competitie %s: %s", code, err, exc_info=True)
                    data[code] = {}

        _LOGGER.info("Football Data update voltooid voor competities: %s", ", ".join(self._competitions))
        return data
