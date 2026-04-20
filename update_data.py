#!/usr/bin/env python3
"""
update_data.py – La Liga 2025/26 data updater
Fetches latest results, standings and power rankings from football-data.org v4
and patches the clubs array in index.html in place.

Usage:
    python update_data.py

Requires FOOTBALL_DATA_API_KEY in .env  (see .env.example).
"""

import os
import re
import shutil
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY     = os.getenv("FOOTBALL_DATA_API_KEY", "")
BASE_URL    = "https://api.football-data.org/v4"
HEADERS     = {"X-Auth-Token": API_KEY}
COMPETITION = "PD"        # La Liga
SEASON      = 2025
INDEX_FILE  = "index.html"
BACKUP_FILE = "index.html.bak"

# ── API team name → our JS club id ──────────────────────────────────────────
CLUB_MAP = {
    "Atlético de Madrid":          "atletico",
    "Atlético Madrid":             "atletico",
    "Club Atlético de Madrid":     "atletico",
    "FC Barcelona":                "barcelona",
    "Real Madrid CF":              "realmadrid",
    "Real Madrid":                 "realmadrid",
    "Villarreal CF":               "villarreal",
    "Athletic Club":               "athleticbilbao",
    "Real Sociedad":               "realsociedad",
    "Real Sociedad de Fútbol":     "realsociedad",
    "Real Betis Balompié":         "betis",
    "Real Betis":                  "betis",
    "RC Celta de Vigo":            "celtavigo",
    "Celta Vigo":                  "celtavigo",
    "Celta de Vigo":               "celtavigo",
    "CA Osasuna":                  "osasuna",
    "Osasuna":                     "osasuna",
    "Girona FC":                   "girona",
    "Girona":                      "girona",
    "RCD Mallorca":                "mallorca",
    "Mallorca":                    "mallorca",
    "Rayo Vallecano":              "rayo",
    "Rayo Vallecano de Madrid":    "rayo",
    "Sevilla FC":                  "sevilla",
    "Sevilla":                     "sevilla",
    "Getafe CF":                   "getafe",
    "Getafe":                      "getafe",
    "Valencia CF":                 "valencia",
    "Valencia":                    "valencia",
    "RCD Espanyol de Barcelona":   "espanyol",
    "RCD Espanyol":                "espanyol",
    "Espanyol":                    "espanyol",
    "Deportivo Alavés":            "alavesdeportivo",
    "Deportivo Alaves":            "alavesdeportivo",
    "Levante UD":                  "levante",
    "Levante":                     "levante",
    "Elche CF":                    "elche",
    "Elche":                       "elche",
    "Real Oviedo":                 "oviedo",
}

# ── Short display names used in the form opponent field ─────────────────────
DISPLAY = {
    "atletico":        "Atlético",
    "barcelona":       "Barcelona",
    "realmadrid":      "Real Madrid",
    "villarreal":      "Villarreal",
    "athleticbilbao":  "Athletic Bilbao",
    "realsociedad":    "Real Sociedad",
    "betis":           "Real Betis",
    "celtavigo":       "Celta Vigo",
    "osasuna":         "Osasuna",
    "girona":          "Girona",
    "mallorca":        "Mallorca",
    "rayo":            "Rayo Vallecano",
    "espanyol":        "Espanyol",
    "alavesdeportivo": "Alavés",
    "levante":         "Levante",
    "elche":           "Elche",
    "oviedo":          "Real Oviedo",
    "getafe":          "Getafe",
    "sevilla":         "Sevilla",
    "valencia":        "Valencia",
}

ALL_IDS = set(DISPLAY.keys())


# ── API helpers ──────────────────────────────────────────────────────────────

def fetch(path, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=HEADERS,
                     params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_finished_matches():
    data = fetch(f"/competitions/{COMPETITION}/matches",
                 {"season": SEASON, "status": "FINISHED"})
    matches = data.get("matches", [])
    # newest matchday first, then by UTC date as tiebreak
    matches.sort(
        key=lambda m: (m.get("matchday", 0), m.get("utcDate", "")),
        reverse=True,
    )
    return matches


def get_standings():
    data = fetch(f"/competitions/{COMPETITION}/standings", {"season": SEASON})
    for group in data.get("standings", []):
        if group.get("type") == "TOTAL":
            return group.get("table", [])
    return []


# ── Match processing ─────────────────────────────────────────────────────────

def resolve(name):
    return CLUB_MAP.get(name)


def result_for(match, club_id):
    """Return (r, score, opp_display, is_home, matchday) from club's POV."""
    home_name = match["homeTeam"]["name"]
    away_name = match["awayTeam"]["name"]
    hg = match["score"]["fullTime"]["home"] or 0
    ag = match["score"]["fullTime"]["away"] or 0
    md = match.get("matchday", 0)

    is_home = (resolve(home_name) == club_id)
    opp_raw = away_name if is_home else home_name
    opp_id  = resolve(opp_raw)
    opp     = DISPLAY.get(opp_id, opp_raw) if opp_id else opp_raw

    gs, ga = (hg, ag) if is_home else (ag, hg)
    score  = f"{gs}-{ga}"
    result = "W" if gs > ga else ("L" if gs < ga else "D")

    return result, score, opp, is_home, md


# ── HTML patching ─────────────────────────────────────────────────────────────

def find_club_block(html, club_id):
    """
    Return (start, end) of the club JS object using brace-depth tracking.
    Looks for the literal opening  {id:"<club_id>"  in the source.
    """
    marker = f'{{id:"{club_id}"'
    start  = html.find(marker)
    if start == -1:
        return -1, -1
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    return -1, -1


def build_form_js(entries):
    """Convert [(r,s,opp,h,md), ...] to a JS array literal."""
    items = []
    for r, s, opp, h, md in entries:
        items.append(f'{{r:"{r}",s:"{s}",opp:"{opp}",h:{"true" if h else "false"},md:{md}}}')
    return "[" + ",".join(items) + "]"


def patch_html(html, updates):
    for club_id, data in updates.items():
        start, end = find_club_block(html, club_id)
        if start == -1:
            print(f"  WARN: block not found for {club_id}")
            continue

        block = html[start:end]

        # form array – entries have no nested arrays so non-greedy [ ... ] is safe
        block = re.sub(
            r"form:\[.*?\]",
            f'form:{data["form_js"]}',
            block, flags=re.DOTALL,
        )

        # pos:N
        block = re.sub(r"\bpos:\d+", f'pos:{data["pos"]}', block)

        # powerRank:N  – update if present, else insert after pos:N
        if re.search(r"\bpowerRank:\d+", block):
            block = re.sub(r"\bpowerRank:\d+",
                           f'powerRank:{data["power_rank"]}', block)
        else:
            block = re.sub(r"(\bpos:\d+)",
                           rf'\1,powerRank:{data["power_rank"]}', block)

        html = html[:start] + block + html[end:]

    return html


# ── Power ranking ─────────────────────────────────────────────────────────────

def calc_power_ranks(standings_by_id, last5_by_id):
    """
    Score = 0.50 × pts_norm  +  0.30 × form_norm  +  0.20 × gd_norm
    Returns dict {club_id: rank (1=best)}.
    """
    all_pts = [s["pts"] for s in standings_by_id.values()]
    all_gd  = [s["gd"]  for s in standings_by_id.values()]
    max_pts  = max(all_pts) if all_pts else 1
    min_gd   = min(all_gd)  if all_gd  else 0
    gd_range = (max(all_gd) - min_gd) if all_gd else 1

    scores = {}
    for cid, st in standings_by_id.items():
        form_pts = sum(
            3 if e[0] == "W" else 1 if e[0] == "D" else 0
            for e in last5_by_id.get(cid, [])
        )
        scores[cid] = (
            0.50 * (st["pts"] / max_pts)
            + 0.30 * (form_pts / 15)          # 5×W = 15 max
            + 0.20 * ((st["gd"] - min_gd) / gd_range)
        )

    ranked = sorted(scores, key=scores.__getitem__, reverse=True)
    return {cid: idx + 1 for idx, cid in enumerate(ranked)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not API_KEY or API_KEY == "your_key_here":
        print("ERROR: Set a valid FOOTBALL_DATA_API_KEY in .env")
        return

    print(f"\n{'═' * 62}")
    print(f"  La Liga 2025/26 · Data Updater")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 62}\n")

    # ── Fetch ────────────────────────────────────────────────────────────
    print("Fetching finished matches …")
    matches = get_finished_matches()
    print(f"  {len(matches)} matches loaded.")

    print("Fetching standings …")
    table = get_standings()
    print(f"  {len(table)} clubs in table.\n")

    # ── Standings dict ───────────────────────────────────────────────────
    standings = {}
    for row in table:
        cid = resolve(row["team"]["name"])
        if cid:
            standings[cid] = {
                "pos": row["position"],
                "pts": row["points"],
                "gd":  row["goalDifference"],
            }

    # ── Last 5 per club ──────────────────────────────────────────────────
    last5 = {cid: [] for cid in ALL_IDS}
    for m in matches:
        h_id = resolve(m["homeTeam"]["name"])
        a_id = resolve(m["awayTeam"]["name"])
        for cid in (h_id, a_id):
            if cid and len(last5[cid]) < 5:
                last5[cid].append(result_for(m, cid))

    # ── Power ranks ──────────────────────────────────────────────────────
    power_rank = calc_power_ranks(standings, last5)

    # ── Build update payload ─────────────────────────────────────────────
    updates = {}
    for cid in ALL_IDS:
        if cid not in standings:
            print(f"  SKIP {cid}: not in standings (may not be in La Liga this season).")
            continue
        if not last5.get(cid):
            print(f"  SKIP {cid}: no finished matches found.")
            continue
        updates[cid] = {
            "form_js":    build_form_js(last5[cid]),
            "pos":        standings[cid]["pos"],
            "power_rank": power_rank.get(cid, 20),
        }

    # ── Patch index.html ─────────────────────────────────────────────────
    with open(INDEX_FILE, encoding="utf-8") as f:
        html = f.read()

    shutil.copy(INDEX_FILE, BACKUP_FILE)
    print(f"Backup saved → {BACKUP_FILE}")

    html = patch_html(html, updates)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{len(updates)}/20 clubs updated in {INDEX_FILE}\n")

    # ── Summary table ────────────────────────────────────────────────────
    col = f"{'Pos':>3}  {'Club ID':<22}  {'PR':>3}  {'Form':<5}  {'MD':>3}  {'Pts':>3}  {'GD':>5}"
    print(col)
    print("─" * len(col))
    for cid, st in sorted(standings.items(), key=lambda x: x[1]["pos"]):
        form_str = "".join(e[0] for e in last5.get(cid, []))
        last_md  = last5[cid][0][4] if last5.get(cid) else "–"
        pr       = power_rank.get(cid, "–")
        print(
            f"{st['pos']:>3}  {cid:<22}  {pr:>3}  {form_str:<5}  "
            f"{last_md:>3}  {st['pts']:>3}  {st['gd']:>5}"
        )

    print(f"\nLast updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    main()
