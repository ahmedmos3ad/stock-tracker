"""EGX (Egyptian Exchange) companies for the symbol dropdown and logo preview.

``_NAMES`` covers the ~100 largest EGX listings (a practical stand-in for the
EGX 100, whose membership is revolving and not published as a static list).
``_DOMAINS`` maps a symbol to the company's website, used only to fetch a logo
on a best-effort basis. Domains are intentionally only set where we're confident
they point at the actual company — everything else falls back to an initials
badge in the UI rather than risk showing the wrong brand's logo.
"""

from __future__ import annotations

from typing import Optional

# Top ~100 EGX companies by market cap (symbol -> display name).
_NAMES: dict[str, str] = {
    "COMI": "Commercial International Bank (CIB)",
    "TMGH": "Talaat Moustafa Group Holding",
    "SWDY": "Elsewedy Electric",
    "ETEL": "Telecom Egypt",
    "EGAL": "Egypt Aluminum (Egyptalum)",
    "MFPC": "Misr Fertilizers Production (MOPCO)",
    "EAST": "Eastern Company",
    "QNBE": "QNB Al Ahli (Qatar National Bank)",
    "ABUK": "Abu Qir Fertilizers",
    "ALCN": "Alexandria Container & Cargo Handling",
    "ORAS": "Orascom Construction",
    "HDBK": "Housing & Development Bank",
    "EFIH": "e-finance",
    "ADIB": "Abu Dhabi Islamic Bank - Egypt",
    "EMFD": "Emaar Misr for Development",
    "FWRY": "Fawry",
    "SCTS": "Suez Canal Co. for Technology Settling",
    "PHDC": "Palm Hills Developments",
    "ORHD": "Orascom Development Egypt",
    "GPPL": "Golden Pyramids Plaza",
    "VLMR": "Valmore Holding",
    "VLMRA": "Valmore Holding (A)",
    "EFID": "Edita Food Industries",
    "HRHO": "EFG Holding",
    "CANA": "Suez Canal Bank",
    "JUFO": "Juhayna Food Industries",
    "BTFH": "Beltone Holding",
    "RAYA": "Raya Holding",
    "IRON": "Egyptian Iron & Steel",
    "FERC": "Ferchem Misr",
    "GBCO": "GB Corp (GB Auto)",
    "CIEB": "Crédit Agricole Egypt",
    "EGCH": "Egyptian Chemical Industries (KIMA)",
    "FAIT": "Faisal Islamic Bank of Egypt",
    "FAITA": "Faisal Islamic Bank of Egypt (USD)",
    "OCDI": "SODIC",
    "HELI": "Heliopolis Housing & Development",
    "EXPA": "Export Development Bank of Egypt",
    "VALU": "valU (U Consumer Finance)",
    "CCAP": "Qalaa Holdings",
    "ARCC": "Arabian Cement Company",
    "CLHO": "Cleopatra Hospitals Group",
    "EGTS": "Egyptian Resorts Company",
    "EFIC": "Egyptian Financial & Industrial",
    "SKPC": "Sidi Kerir Petrochemicals (Sidpec)",
    "TAQA": "TAQA Arabia",
    "MCQE": "Misr Cement (Qena)",
    "POUL": "Cairo Poultry Company",
    "MTIE": "MM Group for Industry & Intl. Trade",
    "EGSA": "Nilesat (Egyptian Satellite)",
    "SCEM": "Sinai Cement",
    "SAUD": "AlBaraka Bank Egypt",
    "ORWE": "Oriental Weavers",
    "UBEE": "The United Bank",
    "CIRA": "CIRA Education",
    "MASR": "Madinet Masr for Housing & Development",
    "PHAR": "EIPICO (Egyptian Intl. Pharmaceuticals)",
    "MBSC": "Misr Beni Suef Cement",
    "ISPH": "Ibnsina Pharma",
    "MHOT": "Misr Hotels Company",
    "EGBE": "Egyptian Gulf Bank",
    "ATQA": "Misr National Steel - Ataqa",
    "CICH": "CI Capital Holding",
    "TALM": "Taaleem Management Services",
    "MOIL": "Maridive & Oil Services",
    "AMOC": "Alexandria Mineral Oils (AMOC)",
    "RMDA": "Rameda Pharma",
    "BINV": "B Investments Holding",
    "IFAP": "Intl. Company for Agricultural Crops",
    "CSAG": "Canal Shipping Agencies",
    "BONY": "Bonyan for Development & Trade",
    "OLFI": "Obour Land for Food Industries",
    "NIPH": "El-Nile Pharmaceuticals",
    "SPHT": "El Shams Pyramids Hotels",
    "ISMQ": "Iron & Steel for Mines & Quarries",
    "MIPH": "Minapharm Pharmaceuticals",
    "ACAP": "A Capital Holding",
    "OIH": "Orascom Investment Holding",
    "ELEC": "Electro Cable Egypt",
    "EGAS": "Egypt Gas Company",
    "SUGR": "Delta Sugar Company",
    "DOMT": "Domty (Arabian Food Industries)",
    "MOIN": "Mohandes Insurance Company",
    "ZMID": "Zahraa El Maadi Investment & Dev.",
    "PRDC": "Pioneers Properties (PRE Group)",
    "MPRC": "Egyptian Media Production City",
    "AMES": "Alexandria New Medical Center",
    "BIOC": "GlaxoSmithKline Egypt",
    "AXPH": "Alexandria Pharmaceuticals",
    "CNFN": "Contact Financial Holding",
    "CPCI": "Kahira Pharmaceuticals & Chemicals",
    "NAPR": "National Printing Company",
    "NINH": "Nozha International Hospital",
    "MPCI": "Memphis Pharmaceuticals",
    "ENGC": "ICON (Industrial Engineering for Construction)",
    "GOUR": "Gourmet Egypt Foods",
    "SPIN": "Alexandria Spinning & Weaving",
    "PHTV": "Pyramisa Hotels & Resorts",
    "DSCW": "Dice Ready-Made Garments",
    "AMIA": "Arab Moltaqa Investments",
    "RACC": "Raya Customer Experience",
}

# Verified company websites for logo lookup. Symbols absent here render an
# initials badge instead of a logo.
_DOMAINS: dict[str, str] = {
    "COMI": "cibeg.com",
    "TMGH": "talaatmoustafa.com",
    "SWDY": "elsewedyelectric.com",
    "ETEL": "te.eg",
    "EGAL": "egyptalum.com.eg",
    "MFPC": "mopco.com",
    "EAST": "easterncompany.com.eg",
    "ABUK": "abuqir.com",
    "ORAS": "orascom.com",
    "EFIH": "efinance.com.eg",
    "ADIB": "adib.eg",
    "FWRY": "fawrypay.com",
    "PHDC": "palmhillsdevelopments.com",
    "ORHD": "orascomdh.com",
    "HRHO": "efghermes.com",
    "CANA": "suezcanalbank.com",
    "JUFO": "juhayna.com",
    "ARCC": "arabiancementcompany.com",
    "SKPC": "sidpec.com",
    "ORWE": "orientalweavers.com",
    "EFID": "edita.com.eg",
    "CLHO": "cleopatrahospitals.com",
    "ISPH": "ibnsina-pharma.com",
    "VALU": "valu.com.eg",
    "TAQA": "taqa.com.eg",
    "CIRA": "ciraeducation.com",
    "GBCO": "ghabbourauto.com",
    "DOMT": "domty.com",
    "RACC": "rayacx.com",
}

COMPANIES: dict[str, dict[str, Optional[str]]] = {
    sym: {"name": name, "domain": _DOMAINS.get(sym)} for sym, name in _NAMES.items()
}


def display_label(symbol: str) -> str:
    info = COMPANIES.get(symbol)
    return f"{symbol} — {info['name']}" if info else symbol


# Verified logo image URLs. ONLY add a symbol here once you've confirmed the URL
# points at the company's actual, correct logo (favicon services are unreliable
# for EGX companies and often return the wrong icon). Anything not listed here
# shows a clean initials badge in the UI instead of risking a wrong logo.
# The browser loads these directly, so SVG/PNG/WebP all work.
_LOGO_OVERRIDES: dict[str, str] = {
    "COMI": "https://upload.wikimedia.org/wikipedia/commons/b/b1/Cib_Logo.svg",
    "ETEL": "https://upload.wikimedia.org/wikipedia/commons/0/0f/We_logo.svg",
    "TMGH": "https://upload.wikimedia.org/wikipedia/commons/5/57/TMG_logo.png",
    "ORAS": "https://static.wikia.nocookie.net/logopedia/images/6/6b/Orascom_Construction_Logo.png",
    "ORHD": "https://upload.wikimedia.org/wikipedia/commons/c/c5/Logo_Orascom.svg",
    "CANA": "https://upload.wikimedia.org/wikipedia/commons/0/0e/Suez_Canal_Bank_Logo.svg",
    "PHDC": "https://upload.wikimedia.org/wikipedia/commons/a/a8/Logo_Palm_Hills_Developments.svg",
    "FWRY": "https://www.fawry.com/wp-content/uploads/2023/05/fawry-Logo.png",
    "MFPC": "https://www.mopco-eg.com/assets/frontend/assets/images/logo.png",
    "ABUK": "https://abuqir.net/wp-content/uploads/2025/11/abuqir-fertlizers-english-logo.webp",
    "EGAL": "https://egyptalum.com.eg/images/logo-wide.png",
    "AMOC": "https://www.amoceg.com/images/logo.png",
    "ADIB": "https://adib.eg/media/146118/logo.png",
    "ARCC": "https://arabiancementcompany.com/wp-content/uploads/2023/11/logo-color.svg",
    "CPCI": "https://kahira-pharmaeg.com/Images/KahiraLogo1.png",
}


def resolve_logo(symbol: str) -> Optional[str]:
    """Return a verified logo URL for the symbol, or None to use an initials badge."""
    return _LOGO_OVERRIDES.get(symbol)
