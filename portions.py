"""
Portion learning. When the user confirms or corrects a meal, we remember the
final numbers for that dish. Next time they log something similar, we feed those
remembered examples into the prompt so estimates match THEIR typical portions.

Keyed loosely by the first couple of significant words of the item name.
"""

import re
import sqlite3
from datetime import datetime

import config
import db

_STOP = {"of", "with", "and", "a", "the", "plate", "bowl", "plates", "bowls",
         "some", "my", "two", "one", "three", "had", "ate", "eaten", "again",
         "for", "in", "on", "just", "i", "today", "lunch", "dinner", "breakfast",
         "khaya", "khaye", "aur", "ek", "do", "teen"}


def _conn():
    return db.connect()


def init_portion_table():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS portion_memory (
                key TEXT PRIMARY KEY,
                item_name TEXT, calories INTEGER, protein_g INTEGER,
                carbs_g INTEGER, fat_g INTEGER, times_seen INTEGER DEFAULT 1,
                updated TEXT
            )""")


def _sig_words(text):
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [w for w in words if w not in _STOP and len(w) > 2]


def _key(item_name):
    """Canonical key = the significant words, sorted, so word order doesn't matter."""
    sig = sorted(set(_sig_words(item_name)))[:3]
    return " ".join(sig)


def remember(d):
    """Record a confirmed food entry as a portion example."""
    key = _key(d.get("item_name", ""))
    if not key:
        return
    now = datetime.now(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        existing = c.execute("SELECT times_seen FROM portion_memory WHERE key=?", (key,)).fetchone()
        if existing:
            # blend toward the newest confirmation (running average, weighted to recent)
            c.execute(
                "UPDATE portion_memory SET calories=(calories+?)/2, protein_g=(protein_g+?)/2, "
                "carbs_g=(carbs_g+?)/2, fat_g=(fat_g+?)/2, item_name=?, "
                "times_seen=times_seen+1, updated=? WHERE key=?",
                (d["calories"], d["protein_g"], d["carbs_g"], d["fat_g"],
                 d["item_name"], now, key))
        else:
            c.execute(
                "INSERT INTO portion_memory (key,item_name,calories,protein_g,carbs_g,"
                "fat_g,times_seen,updated) VALUES (?,?,?,?,?,?,1,?)",
                (key, d["item_name"], d["calories"], d["protein_g"], d["carbs_g"],
                 d["fat_g"], now))


def hint_for(text):
    """
    Return a short prompt hint if we've learned this user's portion for the
    dish they're describing, else ''. Matches by significant-word overlap so
    "had chicken biryani again" still finds the learned "Chicken biryani".
    """
    words = set(_sig_words(text))
    if not words:
        return ""
    with _conn() as c:
        rows = c.execute("SELECT * FROM portion_memory WHERE times_seen>=2").fetchall()
    best, best_overlap = None, 0
    for r in rows:
        kw = set(r["key"].split())
        overlap = len(words & kw)
        # need to match most of the learned dish's words
        if overlap and overlap >= len(kw) - 0 and overlap > best_overlap:
            best, best_overlap = r, overlap
    if not best:
        return ""
    return (f"\n\nLEARNED PORTION (this user's typical '{best['item_name']}' is about "
            f"{best['calories']} kcal, {best['protein_g']}g protein, {best['carbs_g']}g carbs, "
            f"{best['fat_g']}g fat). Lean toward these numbers unless they clearly "
            f"describe a different amount.")