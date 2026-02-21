"""
seed_castaways.py
=================
Scrapes Survivor 50 cast data from EW and seeds the Supabase `castaways` table.

Before running:
  1. Run schema_v2.sql in Supabase SQL Editor (adds tribe/photo_url/etc. columns)
  2. pip install -r requirements.txt

Usage:
  python seed_castaways.py            # dry-run: prints parsed data, does NOT write
  python seed_castaways.py --insert   # parses AND inserts into Supabase
"""

import re
import sys
import toml
import requests
from bs4 import BeautifulSoup, Tag
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────

EW_URL = (
    "https://ew.com/survivor-50-official-cast-photos-bios-tribe-divisions-revealed-11891991"
)

# Tribe assignments (sourced from the EW article tribal-overview section).
# Key = player_name as it appears in the <h3> headers on the page.
TRIBE_MAP = {
    "Colby Donaldson": "Vatu",
    "Stephenie LaGrossa Kendrick": "Vatu",
    'Quintavius "Q" Burdette': "Vatu",
    "Genevieve Mushaluk": "Vatu",
    "Kyle Fraser": "Vatu",
    "Rizo Velovic": "Vatu",
    "Aubry Bracco": "Vatu",
    "Angelina Keeley": "Vatu",
    "Ozzy Lusth": "Cila",
    "Joe Hunter": "Cila",
    "Christian Hubicki": "Cila",
    "Rick Devens": "Cila",
    "Savannah Louie": "Cila",
    "Cirie Fields": "Cila",
    "Emily Flippen": "Cila",
    "Jenna Lewis-Dougherty": "Cila",
    'Benjamin "Coach" Wade': "Kalo",
    "Kamilla Karthigesu": "Kalo",
    "Mike White": "Kalo",
    "Charlie Davis": "Kalo",
    "Tiffany Ervin": "Kalo",
    "Dee Valladares": "Kalo",
    "Jonathan Young": "Kalo",
    "Chrissy Hofbeck": "Kalo",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract(text: str, label: str, stop_labels: list[str]) -> str | None:
    """Pull a bio field value out of the concatenated bio string."""
    stops = "|".join(re.escape(s) for s in stop_labels)
    pattern = rf"{re.escape(label)}:\s*(.*?)(?={stops}:)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else None


def _clean(text: str) -> str:
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", text).strip()


# ── Scraper ───────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://google.com",
    }

    print(f"Fetching {EW_URL} …")
    resp = requests.get(EW_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # EW wraps each bio in a structured content block.
    # The cast-bio h3 headers appear inside the article body.
    # We walk all <h3> tags and collect those whose text matches a known cast member.
    known_names = set(TRIBE_MAP.keys())
    castaways: list[dict] = []

    article = soup.find("article") or soup.find("main") or soup.body

    for h3 in article.find_all("h3"):
        raw_name = _clean(h3.get_text())
        if raw_name not in known_names:
            continue

        # ── Photo URL ─────────────────────────────────────────────────────────
        # Walk siblings until we find an <img> tag or hit the next castaway h3.
        photo_url: str | None = None
        for sib in h3.next_siblings:
            if not isinstance(sib, Tag):   # skip NavigableString / Comment nodes
                continue
            if sib.name == "h3":           # hit the next castaway — stop
                break
            # The sibling itself might be the img, or it might contain one
            img: Tag | None = sib if sib.name == "img" else sib.find("img")
            if isinstance(img, Tag):
                photo_url = img.get("data-src") or img.get("src")
                if photo_url and "placeholder" not in photo_url:
                    break

        # ── Bio text ──────────────────────────────────────────────────────────
        # Walk the same siblings used for the photo, collecting text only between
        # this h3 and the next castaway h3.  Avoids the parent.get_text() bug
        # where every castaway shared the same full-article text block.
        bio_parts: list[str] = []
        for sib in h3.next_siblings:
            if not isinstance(sib, Tag):
                txt = str(sib).strip()
                if txt:
                    bio_parts.append(txt)
                continue
            if sib.name == "h3":          # next castaway section — stop
                break
            bio_parts.append(sib.get_text(separator=" "))
        bio_block = _clean(" ".join(bio_parts))

        stop_questions = [
            "Why do you want",
            "What one life",
            "Coming into this",
            "What would you say",
            "What is your strategy",
            "Survivor fans",
            "At this point",
            "Why will you",
        ]

        seasons_played = (
            _extract(bio_block, "Seasons", ["Age"])
            or _extract(bio_block, "Season", ["Age"])
        )
        age_str = _extract(bio_block, "Age", ["Hometown"])
        hometown = _extract(bio_block, "Hometown", ["Current Residence"])
        occupation_raw = _extract(bio_block, "Current Occupation", stop_questions)

        castaways.append(
            {
                "player_name": raw_name,
                "tribe": TRIBE_MAP[raw_name],
                "seasons_played": seasons_played,
                "age": int(age_str) if age_str and age_str.isdigit() else None,
                "hometown": hometown,
                "occupation": occupation_raw,
                "photo_url": photo_url,
                "status": "Active",
            }
        )

    # Sort by tribe then name for readability
    castaways.sort(key=lambda c: (c["tribe"], c["player_name"]))
    return castaways


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    insert = "--insert" in sys.argv

    castaways = scrape()

    print(f"\nParsed {len(castaways)} castaways:\n")
    col_w = 34
    print(f"{'Name':<{col_w}} {'Tribe':<8} {'Age':<5} {'Seasons'}")
    print("-" * 80)
    for c in castaways:
        print(
            f"{c['player_name']:<{col_w}} "
            f"{c['tribe']:<8} "
            f"{str(c['age'] or '?'):<5} "
            f"{c['seasons_played'] or '—'}"
        )

    if not insert:
        print("\n⚠️  Dry-run mode. Run with --insert to write to Supabase.")
        return

    # ── Load credentials ──────────────────────────────────────────────────────
    with open(".streamlit/secrets.toml") as f:
        secrets = toml.load(f)

    sb = create_client(secrets["supabase"]["url"], secrets["supabase"]["key"])

    print("\nInserting into Supabase …")
    for c in castaways:
        try:
            sb.table("castaways").upsert(c, on_conflict="player_name").execute()
            print(f"  ✓  {c['player_name']}")
        except Exception as e:
            print(f"  ✗  {c['player_name']}: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
