"""ClinicalTrials.gov API v2 client: real, live trial data (public, no key needed)."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .net import USER_AGENT, ssl_context

BASE = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = ",".join([
    "NCTId", "BriefTitle", "OverallStatus", "Condition", "Phase", "BriefSummary", "InterventionName",
    "EligibilityCriteria", "MinimumAge", "MaximumAge", "Sex", "HealthyVolunteers",
    "LocationFacility", "LocationCity", "LocationState", "LocationCountry", "LocationStatus",
    "LeadSponsorName",
])


class TrialsError(Exception):
    pass


def _get(d: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
    return default if d is None else d


def summarize(study: dict, near: str = "", detail: bool = False) -> dict:
    ps = study.get("protocolSection", {})
    nct = _get(ps, "identificationModule", "nctId", default="")
    locations = _get(ps, "contactsLocationsModule", "locations", default=[]) or []
    needle = (near or "").lower().strip()

    def label(loc: dict) -> str:
        parts = [loc.get("facility"), loc.get("city"), loc.get("state"), loc.get("country")]
        return ", ".join(p for p in parts if p)

    matching = [loc for loc in locations if needle and needle in label(loc).lower()]
    shown = (matching or locations)[:3]
    out = {
        "nct_id": nct,
        "title": _get(ps, "identificationModule", "briefTitle", default=""),
        "status": _get(ps, "statusModule", "overallStatus", default=""),
        "phases": _get(ps, "designModule", "phases", default=[]),
        "conditions": (_get(ps, "conditionsModule", "conditions", default=[]) or [])[:5],
        "interventions": [i.get("name") for i in (_get(ps, "armsInterventionsModule", "interventions", default=[]) or [])][:4],
        "sponsor": _get(ps, "sponsorCollaboratorsModule", "leadSponsor", "name", default=""),
        "ages": f"{_get(ps, 'eligibilityModule', 'minimumAge', default='any')} to "
                f"{_get(ps, 'eligibilityModule', 'maximumAge', default='any')}",
        "sex": _get(ps, "eligibilityModule", "sex", default="ALL"),
        "healthy_volunteers": _get(ps, "eligibilityModule", "healthyVolunteers", default=False),
        "sites_total": len(locations),
        "sites_near_query": len(matching),
        "sites_shown": [label(loc) for loc in shown],
        "url": f"https://clinicaltrials.gov/study/{nct}" if nct else "",
    }
    summary = _get(ps, "descriptionModule", "briefSummary", default="")
    out["summary"] = summary[:1500] if detail else summary[:280]
    if detail:
        out["eligibility_criteria"] = _get(ps, "eligibilityModule", "eligibilityCriteria", default="")[:5000]
    return out


class ClinicalTrials:
    def __init__(self, timeout: float = 15.0, cache_seconds: float = 600.0) -> None:
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _fetch(self, url: str) -> Any:
        with self._lock:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < self.cache_seconds:
                return hit[1]
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ssl_context()) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise TrialsError("not_found")
            raise TrialsError(f"clinicaltrials.gov returned {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TrialsError(f"clinicaltrials.gov unreachable: {e}")
        with self._lock:
            self._cache[url] = (time.time(), data)
        return data

    def search(self, condition: str, location: str = "", max_results: int = 3,
               recruiting_only: bool = True) -> list[dict]:
        params = {"query.cond": condition, "pageSize": str(max(1, min(int(max_results or 3), 5))),
                  "fields": FIELDS, "format": "json"}
        if location:
            params["query.locn"] = location
        if recruiting_only:
            params["filter.overallStatus"] = "RECRUITING"
        data = self._fetch(f"{BASE}?{urllib.parse.urlencode(params)}")
        return [summarize(s, near=location) for s in data.get("studies", [])]

    def get(self, nct_id: str, near: str = "") -> Optional[dict]:
        nct = (nct_id or "").strip().upper()
        if not (nct.startswith("NCT") and nct[3:].isdigit() and len(nct) == 11):
            raise TrialsError("invalid_nct_id")
        data = self._fetch(f"{BASE}/{nct}?{urllib.parse.urlencode({'fields': FIELDS, 'format': 'json'})}")
        return summarize(data, near=near, detail=True)


class OfflineTrials:
    """Stand-in for tests and offline evals. Returns an obviously fake record."""

    def search(self, condition: str, location: str = "", max_results: int = 3, recruiting_only: bool = True) -> list[dict]:
        return [{
            "nct_id": "NCT00000000", "title": f"[OFFLINE STUB] Example study for {condition}", "status": "RECRUITING",
            "phases": ["PHASE2"], "conditions": [condition], "interventions": ["Example drug"], "sponsor": "Stub",
            "ages": "18 Years to 65 Years", "sex": "ALL", "healthy_volunteers": False, "sites_total": 1,
            "sites_near_query": 1, "sites_shown": [location or "Anywhere"], "url": "", "summary": "Offline stub record.",
        }]

    def get(self, nct_id: str, near: str = "") -> Optional[dict]:
        record = self.search("example condition", near)[0]
        record["nct_id"] = nct_id
        record["eligibility_criteria"] = "Inclusion Criteria:\n* Age 18-65\n* Diagnosis of the condition\n\nExclusion Criteria:\n* Pregnancy"
        return record
