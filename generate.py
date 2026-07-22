import os
import re
import json
import random
import asyncio
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import formatdate
import time
import edge_tts
from google import genai

# ─── Konfiguration ───────────────────────────────────────────────────────────

KATEGORIE_NAME = "Comedy & Unterhaltung"
ITUNES_GENRE_COMEDY = "1303"      # iTunes-Genre, nur als Namensregister der Comedy-Shows
POOL_GROESSE = 100                # so viele Comedy-Shows in den Auswahl-Pool holen
ANZAHL_PRO_TAG = 3               # 3 zufaellige Podcasts pro Tag
WIEDERHOLUNG_TAGE = 14           # gewaehlte Shows fruehestens nach 14 Tagen wieder
VERLAUF_PFAD = "verlauf.json"    # merkt sich, welche Show wann dran war

PODCAST_NAME = "Podcast Entdeckungen"
PODCAST_BESCHREIBUNG = "Taeglich drei zufaellig entdeckte Comedy- und Unterhaltungs-Podcasts aus dem deutschsprachigen Raum - mit Begeisterung vorgestellt."
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "http://localhost")
KONTAKT_EMAIL = os.environ.get("KONTAKT_EMAIL", "")

# ─── Hilfsfunktion: Text für XML bereinigen ──────────────────────────────────

def xml_sicher(text: str) -> str:
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

# ─── Schritt 1: Comedy-Pool von iTunes holen (nur Namensliste) ───────────────

def hole_comedy_pool(land: str = "de") -> list:
    url = f"https://itunes.apple.com/{land}/rss/toppodcasts/limit={POOL_GROESSE}/genre={ITUNES_GENRE_COMEDY}/json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        daten = response.json()
        podcasts = []
        for eintrag in daten.get("feed", {}).get("entry", []):
            link = eintrag.get("id", {}).get("label", "")
            itunes_id = ""
            if "/id" in link:
                itunes_id = link.split("/id")[-1].split("?")[0]
            podcasts.append({
                "name": eintrag.get("im:name", {}).get("label", ""),
                "itunes_id": itunes_id,
            })
        return podcasts
    except Exception as e:
        print(f"Fehler beim Laden der Comedy-Liste: {e}")
        return []

# ─── Schritt 2: Zufallsauswahl mit 14-Tage-Sperre ────────────────────────────

def podcast_schluessel(podcast: dict) -> str:
    return podcast.get("itunes_id") or podcast.get("name", "")

def lade_verlauf() -> dict:
    if os.path.exists(VERLAUF_PFAD):
        try:
            with open(VERLAUF_PFAD, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  Verlauf konnte nicht geladen werden: {e}")
    return {}

def speichere_verlauf(verlauf: dict):
    heute = datetime.now().date()
    sauber = {}
    for schluessel, datum_str in verlauf.items():
        try:
            datum = datetime.strptime(datum_str, "%Y-%m-%d").date()
            if (heute - datum).days < WIEDERHOLUNG_TAGE:
                sauber[schluessel] = datum_str
        except Exception:
            continue
    try:
        with open(VERLAUF_PFAD, "w", encoding="utf-8") as f:
            json.dump(sauber, f, ensure_ascii=False, indent=2)
        print(f"Verlauf gespeichert ({len(sauber)} Shows in den letzten {WIEDERHOLUNG_TAGE} Tagen gesperrt)")
    except Exception as e:
        print(f"  Verlauf konnte nicht gespeichert werden: {e}")

def waehle_kandidaten(pool: list, verlauf: dict) -> list:
    heute = datetime.now().date()
    gesperrt = set()
    for schluessel, datum_str in verlauf.items():
        try:
            datum = datetime.strptime(datum_str, "%Y-%m-%d").date()
            if (heute - datum).days < WIEDERHOLUNG_TAGE:
                gesperrt.add(schluessel)
        except Exception:
            continue

    verfuegbar = [p for p in pool if podcast_schluessel(p) not in gesperrt]
    if len(verfuegbar) < ANZAHL_PRO_TAG:
        print(f"  Nur {len(verfuegbar)} freie Shows - Sperre wird fuer diesen Lauf ignoriert")
        verfuegbar = list(pool)

    random.shuffle(verfuegbar)

    gesehen = set()
    kandidaten = []
    for p in verfuegbar:
        k = podcast_schluessel(p)
        if k in gesehen:
            continue
        gesehen.add(k)
        kandidaten.append(p)
    return kandidaten

# ─── Schritt 3: Spotify - Show finden und neueste Episode holen ───────────────

def hole_spotify_token() -> str:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return ""
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("access_token", "")
    except Exception as e:
        print(f"  Spotify Token Fehler: {e}")
        return ""

def finde_spotify_show(name: str, token: str) -> dict:
    if not token or not name:
        return {}
    try:
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": name, "type": "show", "market": "DE", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        resp.raise_for_status()
        items = [i for i in resp.json().get("shows", {}).get("items", []) if i]
        if not items:
            return {}
        show = items[0]
        return {
            "id": show.get("id", ""),
            "name": show.get("name", name),
            "publisher": show.get("publisher", ""),
            "beschreibung": re.sub(r"<[^>]+>", "", show.get("description", "")).strip(),
            "link": show.get("external_urls", {}).get("spotify", ""),
        }
    except Exception as e:
        print(f"  Spotify Show-Suche Fehler: {e}")
        return {}

def hole_neueste_spotify_episode(show_id: str, token: str) -> dict:
    leer = {"episode_titel": "", "episode_beschreibung": ""}
    if not token or not show_id:
        return leer
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/shows/{show_id}/episodes",
            params={"market": "DE", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        resp.raise_for_status()
        items = [i for i in resp.json().get("items", []) if i]
        if not items:
            return leer
        ep = items[0]
        beschreibung = re.sub(r"<[^>]+>", "", ep.get("description", "")).strip()[:1500]
        titel = ep.get("name", "").strip()
        print(f"  Neueste Spotify-Episode: {titel[:60]}")
        return {"episode_titel": titel, "episode_beschreibung": beschreibung}
    except Exception as e:
        print(f"  Spotify Episoden-Abruf Fehler: {e}")
        return leer

# ─── Schritt 4: Zusammenfassung mit Gemini generieren (ca. 3 Minuten) ─────────

def generiere_skript(kategorie_name: str, podcast: dict, episode: dict) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    hat_episode = bool(episode.get("episode_titel"))

    if hat_episode:
        kontext = (
            f"Podcast: {podcast['name']} von {podcast['autor']}\n"
            f"Neueste Episode: {episode['episode_titel']}\n"
            f"Episoden-Inhalt: {episode['episode_beschreibung']}"
        )
        aufgabe = (
            "Erzaehl ausfuehrlich und mit Begeisterung, worum es in dieser konkreten Episode geht. "
            "Nenne den Episodentitel, geh auf mehrere Punkte und Momente ein, "
            "und erklaere warum diese Episode hoerenswert ist."
        )
    else:
        kontext = (
            f"Podcast: {podcast['name']} von {podcast['autor']}\n"
            f"Beschreibung: {episode['episode_beschreibung']}"
        )
        aufgabe = (
            "Erzaehl ausfuehrlich und mit Begeisterung, worum es in diesem Podcast geht. "
            "Erklaere das Konzept, den Humor, den Stil und warum Hoerer ihn lieben werden."
        )

    prompt = (
        "Du bist eine Podcast-Moderatorin und hast gerade diese Folge gehoert. "
        "Du bist total begeistert und erzaehlst einer Freundin ausfuehrlich davon. "
        "Schreibe einen lebendigen, gesprochenen Text auf Deutsch, der ca. 480 bis 520 Woerter "
        "lang ist (das entspricht etwa 3 Minuten gesprochen).\n\n"
        f"{kontext}\n\n"
        f"{aufgabe}\n\n"
        "Regeln:\n"
        "- Starte direkt mit dem Podcast- oder Episodennamen\n"
        "- Enthusiastisch, mit Energie, wie ein echter Mensch\n"
        "- Geh ausfuehrlich auf mehrere Aspekte ein: worum es geht, was das Besondere ist, "
        "ein bis zwei konkrete Details oder Momente, und fuer wen sich das Reinhoeren lohnt\n"
        "- Konkret bleiben, nicht nur allgemeine Phrasen\n"
        "- Kurze Ausrufe zwischendurch sind gut\n"
        "- Kein Hinweis auf Links oder Shownotes\n"
        "- Keine Sonderzeichen, keine Emojis, keine Aufzaehlungen\n"
        "- Wirklich ca. 480 bis 520 Woerter schreiben, nicht kuerzer\n\n"
        "Antworte NUR mit dem Sprechtext.\n\n"
        "Gib danach in einer neuen Zeile 'EMPFEHLUNG: [Podcast-Name]' an."
    )

    for versuch in range(6):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            text = (response.text or "").strip()

            zeilen = text.split("\n")
            skript_zeilen = []
            empfohlener_podcast = podcast["name"]

            for zeile in zeilen:
                if zeile.startswith("EMPFEHLUNG:"):
                    empfohlener_podcast = zeile.replace("EMPFEHLUNG:", "").strip()
                else:
                    skript_zeilen.append(zeile)

            skript = "\n".join(skript_zeilen).strip()
            wortanzahl = len(skript.split())

            if wortanzahl < 400:
                print(f"  Skript zu kurz ({wortanzahl} Woerter) - versuche erneut")
                time.sleep(5)
                continue

            return {
                "skript": skript,
                "empfohlener_podcast": empfohlener_podcast,
                "empfohlener_link": podcast["link"],
                "episode_titel": episode.get("episode_titel", ""),
            }

        except Exception as e:
            wartezeit = 10 * (versuch + 1)
            print(f"  Gemini Fehler (Versuch {versuch+1}/6): {e}")
            print(f"  Warte {wartezeit} Sekunden...")
            time.sleep(wartezeit)

    print("  Alle Versuche fehlgeschlagen - nutze Fallback")
    fallback = (
        f"{podcast['name']} ist gerade einer der beliebtesten deutschsprachigen Comedy-Podcasts. "
        f"Von {podcast['autor']} gemacht, bringt die Show genau die Mischung aus Humor, lockeren "
        f"Gespraechen und ueberraschenden Momenten, fuer die man Comedy-Podcasts einfach liebt. "
        f"Es wird ueber alles geredet, was gerade so passiert, immer mit einem Augenzwinkern und ohne "
        f"sich selbst zu ernst zu nehmen. Man merkt sofort, dass hier Leute am Werk sind, die richtig "
        f"Spass an der Sache haben, und genau das springt beim Zuhoeren ueber. Wer im Alltag, beim "
        f"Pendeln oder beim Sport etwas zum Lachen und gute Laune sucht, ist hier absolut richtig. "
        f"Unbedingt reinhoeren und selbst ueberzeugen lassen!"
    )
    return {
        "skript": fallback,
        "empfohlener_podcast": podcast["name"],
        "empfohlener_link": podcast["link"],
        "episode_titel": "",
    }

# ─── Schritt 5: Audio mit Edge-TTS generieren (kostenlos, kein API-Key) ───────

def generiere_audio(skript: str, dateiname: str) -> bool:
    stimme = "de-DE-KatjaNeural"  # Weibliche deutsche Stimme, hohe Qualitaet
    pfad = f"audio/{dateiname}"

    async def _synthese():
        communicate = edge_tts.Communicate(skript, stimme, rate="+5%")
        await communicate.save(pfad)

    for versuch in range(3):
        try:
            os.makedirs("audio", exist_ok=True)
            asyncio.run(_synthese())

            if os.path.exists(pfad) and os.path.getsize(pfad) > 0:
                print(f"  Audio gespeichert: {pfad} ({os.path.getsize(pfad)} Bytes)")
                return True

            print("  Edge-TTS: Datei ist leer oder fehlt - versuche erneut")
            time.sleep(5)

        except Exception as e:
            print(f"  Fehler bei Edge-TTS (Versuch {versuch+1}/3): {e}")
            time.sleep(5)

    return False

# ─── Schritt 6: RSS Feed aktualisieren ───────────────────────────────────────

def aktualisiere_rss_feed(episoden: list):
    feed_pfad = "feed.xml"
    ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")

    owner_block = ""
    if KONTAKT_EMAIL:
        owner_block = f"""
    <itunes:owner>
      <itunes:name>Podcast Entdeckungen</itunes:name>
      <itunes:email>{KONTAKT_EMAIL}</itunes:email>
    </itunes:owner>"""

    rss_string = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{PODCAST_NAME}</title>
    <description>{PODCAST_BESCHREIBUNG}</description>
    <link>{GITHUB_PAGES_URL}</link>
    <language>de</language>
    <itunes:author>Podcast Entdeckungen</itunes:author>{owner_block}
    <itunes:category text="Comedy"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{GITHUB_PAGES_URL}/cover.jpg"/>
"""

    neue_guids = {ep["guid"] for ep in episoden}
    alte_episoden = []
    if os.path.exists(feed_pfad):
        try:
            tree = ET.parse(feed_pfad)
            root = tree.getroot()
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item"):
                    guid = item.findtext("guid", "")
                    if guid not in neue_guids:
                        alte_episoden.append(ET.tostring(item, encoding="unicode"))
        except Exception:
            pass

    for ep in episoden:
        datum_rfc = formatdate(ep["timestamp"])
        titel_sicher = xml_sicher(ep["titel"])
        podcast_name_sicher = xml_sicher(ep["podcast_name"])
        episode_titel_sicher = xml_sicher(ep.get("episode_titel", ""))
        episode_info = f"Episode: {episode_titel_sicher}\n\n" if episode_titel_sicher else ""
        link_ziel = ep.get("spotify_link") or ep["podcast_link"]
        link_label = "Auf Spotify hoeren" if ep.get("spotify_link") else "Zum Podcast"

        rss_string += f"""
    <item>
      <title>{titel_sicher}</title>
      <description><![CDATA[{episode_info}{ep["beschreibung"]}

{link_label}: <a href="{link_ziel}">{podcast_name_sicher}</a>

Erstellt von Podcast Entdeckungen. Alle Rechte am empfohlenen Podcast liegen beim jeweiligen Urheber.]]></description>
      <enclosure url="{GITHUB_PAGES_URL}/audio/{ep["dateiname"]}" type="audio/mpeg" length="0"/>
      <guid isPermaLink="false">{ep["guid"]}</guid>
      <pubDate>{datum_rfc}</pubDate>
      <itunes:duration>180</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>"""

    for alte_ep in alte_episoden[:27]:
        rss_string += f"\n    {alte_ep}"

    rss_string += """
  </channel>
</rss>"""

    with open(feed_pfad, "w", encoding="utf-8") as f:
        f.write(rss_string)

    print(f"RSS Feed aktualisiert mit {len(episoden)} neuen Episoden")

# ─── Schritt 7: Website aktualisieren ────────────────────────────────────────

def aktualisiere_website(episoden: list):
    heute = datetime.now().strftime("%d.%m.%Y")

    episoden_html = ""
    for ep in episoden:
        episode_zeile = f'<p class="episode-titel">Neueste Episode: {ep["episode_titel"]}</p>' if ep.get("episode_titel") else ""
        link_ziel = ep.get("spotify_link") or ep["podcast_link"]
        link_label = "Auf Spotify hoeren" if ep.get("spotify_link") else "Zum Podcast"

        episoden_html += f"""
        <div class="episode-card">
          <div class="kategorie-badge">{ep['kategorie']}</div>
          <h2>{ep['podcast_name']}</h2>
          {episode_zeile}
          <audio controls>
            <source src="audio/{ep['dateiname']}" type="audio/mpeg">
          </audio>
          <p><a href="{link_ziel}" target="_blank">{link_label} &rarr;</a></p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Podcast Entdeckungen - Die besten Comedy-Podcasts taeglich</title>
  <meta name="description" content="Taeglich drei zufaellig entdeckte Comedy- und Unterhaltungs-Podcasts aus dem deutschsprachigen Raum.">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; color: #333; }}
    h1 {{ color: #1a1a2e; font-size: 2em; }}
    .subtitle {{ color: #666; margin-top: -10px; }}
    .episode-card {{ background: white; border-radius: 12px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .kategorie-badge {{ display: inline-block; background: #1a1a2e; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.8em; margin-bottom: 10px; }}
    h2 {{ margin: 8px 0; font-size: 1.3em; }}
    .episode-titel {{ color: #666; font-size: 0.9em; margin: 4px 0 12px 0; }}
    audio {{ width: 100%; margin: 12px 0; }}
    a {{ color: #e94560; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .rss-link {{ background: #f8a500; color: white; padding: 10px 20px; border-radius: 8px; display: inline-block; margin-top: 20px; }}
    footer {{ text-align: center; color: #999; margin-top: 40px; font-size: 0.85em; }}
  </style>
</head>
<body>
  <h1>Podcast Entdeckungen</h1>
  <p class="subtitle">Taeglich drei Comedy-Podcasts zufaellig entdeckt - kuratiert mit KI</p>
  <p><strong>Heute, {heute}:</strong></p>
  {episoden_html}
  <p><a href="feed.xml" class="rss-link">RSS Feed abonnieren</a></p>
  <footer>
    Podcast Entdeckungen - Automatisch generiert. Alle empfohlenen Podcasts sind Eigentum ihrer jeweiligen Urheber.
  </footer>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Website aktualisiert")

# ─── Hauptprogramm ───────────────────────────────────────────────────────────

def main():
    print(f"Starte Generierung - {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    heute = datetime.now().strftime("%Y-%m-%d")
    episoden = []

    spotify_token = hole_spotify_token()
    if not spotify_token:
        print("WARNUNG: Kein Spotify-Token - ohne Spotify kann nichts generiert werden.")
        return

    pool = hole_comedy_pool()
    if not pool:
        print("Keine Comedy-Podcasts gefunden - Abbruch.")
        return
    print(f"{len(pool)} Comedy-Shows im Pool")

    verlauf = lade_verlauf()
    kandidaten = waehle_kandidaten(pool, verlauf)
    print(f"{len(kandidaten)} moegliche Shows nach Zufalls-Shuffle und {WIEDERHOLUNG_TAGE}-Tage-Sperre")

    for podcast in kandidaten:
        if len(episoden) >= ANZAHL_PRO_TAG:
            break

        nummer = len(episoden) + 1
        print(f"\nPruefe Show: {podcast['name']}")

        show = finde_spotify_show(podcast["name"], spotify_token)
        if not show or not show.get("id") or not show.get("link"):
            print("  Nicht auf Spotify gefunden - ueberspringe")
            continue
        print(f"  Spotify-Show: {show['name']} -> {show['link']}")

        episode = hole_neueste_spotify_episode(show["id"], spotify_token)
        if not episode.get("episode_beschreibung"):
            episode["episode_beschreibung"] = show.get("beschreibung", "")

        podcast_daten = {
            "name": show["name"],
            "autor": show.get("publisher", ""),
            "beschreibung": show.get("beschreibung", ""),
            "link": show["link"],
        }

        ergebnis = generiere_skript(KATEGORIE_NAME, podcast_daten, episode)
        print(f"  Skript: {len(ergebnis['skript'].split())} Woerter")

        dateiname = f"{heute}-comedy-{nummer}.mp3"
        audio_ok = generiere_audio(ergebnis["skript"], dateiname)

        if not audio_ok:
            print("  Audio fehlgeschlagen - ueberspringe, Show bleibt frei")
            continue

        # Show fuer WIEDERHOLUNG_TAGE Tage sperren
        verlauf[podcast_schluessel(podcast)] = heute

        titel = show["name"]
        if episode.get("episode_titel"):
            titel += f" - {episode['episode_titel'][:60]}"

        episoden.append({
            "titel": titel,
            "beschreibung": ergebnis["skript"],
            "dateiname": dateiname,
            "podcast_name": show["name"],
            "podcast_link": show["link"],
            "spotify_link": show["link"],
            "episode_titel": episode.get("episode_titel", ""),
            "kategorie": KATEGORIE_NAME,
            "guid": f"{heute}-comedy-{nummer}",
            "timestamp": time.time()
        })

        time.sleep(2)

    if episoden:
        aktualisiere_rss_feed(episoden)
        aktualisiere_website(episoden)
        speichere_verlauf(verlauf)
        print(f"\nFertig! {len(episoden)} Episoden generiert.")
    else:
        print("\nKeine Episoden generiert.")

if __name__ == "__main__":
    main()
