import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import formatdate
import time
from google import genai

# ─── Konfiguration ───────────────────────────────────────────────────────────

KATEGORIEN = {
    "true-crime": {
        "name": "True Crime",
        "itunes_genre_id": "1488",
    },
    "business": {
        "name": "Business",
        "itunes_genre_id": "1321",
    },
    "nachrichten": {
        "name": "Nachrichten",
        "itunes_genre_id": "1526",
    }
}

PODCAST_NAME = "Podcast Entdeckungen"
PODCAST_BESCHREIBUNG = "Taeglich die besten deutschsprachigen Podcasts entdecken - True Crime, Business und Nachrichten."
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "http://localhost")

# Bella - weibliche Stimme
ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"

# ─── Hilfsfunktion: Text für XML bereinigen ───────────────────────────────────

def xml_sicher(text: str) -> str:
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

# ─── Schritt 1: Top Podcasts von iTunes holen ────────────────────────────────

def hole_top_podcasts(genre_id: str, land: str = "de") -> list:
    url = f"https://itunes.apple.com/{land}/rss/toppodcasts/limit=10/genre={genre_id}/json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        daten = response.json()
        podcasts = []
        for eintrag in daten.get("feed", {}).get("entry", []):
            podcasts.append({
                "name": eintrag.get("im:name", {}).get("label", ""),
                "autor": eintrag.get("im:artist", {}).get("label", ""),
                "beschreibung": eintrag.get("summary", {}).get("label", ""),
                "link": eintrag.get("id", {}).get("label", ""),
            })
        return podcasts
    except Exception as e:
        print(f"Fehler beim Laden der iTunes Charts: {e}")
        return []

# ─── Schritt 2: Neueste Episode aus dem Podcast-RSS-Feed holen ───────────────

def hole_neueste_episode(podcast: dict) -> dict:
    itunes_link = podcast.get("link", "")
    itunes_id = ""
    if "/id" in itunes_link:
        itunes_id = itunes_link.split("/id")[-1].split("?")[0]

    feed_url = ""

    if itunes_id:
        try:
            lookup_url = f"https://itunes.apple.com/lookup?id={itunes_id}&entity=podcast"
            resp = requests.get(lookup_url, timeout=10)
            resp.raise_for_status()
            daten = resp.json()
            if daten.get("results"):
                feed_url = daten["results"][0].get("feedUrl", "")
                print(f"  Feed-URL gefunden: {feed_url}")
        except Exception as e:
            print(f"  iTunes Lookup fehlgeschlagen: {e}")

    if not feed_url:
        print(f"  Keine Feed-URL - nutze Podcast-Beschreibung")
        return {
            "episode_titel": "",
            "episode_beschreibung": podcast.get("beschreibung", ""),
        }

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; PodcastEntdeckungen/1.0)"}
        resp = requests.get(feed_url, timeout=15, headers=headers)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            raise ValueError("Kein <channel> im Feed")

        item = channel.find("item")
        if item is None:
            raise ValueError("Keine Episoden im Feed")

        titel = item.findtext("title", "").strip()

        beschreibung = item.findtext("description", "").strip()
        if not beschreibung:
            ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
            beschreibung = item.findtext("itunes:summary", "", ns).strip()

        # HTML-Tags entfernen
        beschreibung = re.sub(r"<[^>]+>", "", beschreibung)
        # Sonderzeichen bereinigen
        beschreibung = beschreibung.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        beschreibung = beschreibung[:800]

        print(f"  Neueste Episode: {titel[:60]}...")
        return {
            "episode_titel": titel,
            "episode_beschreibung": beschreibung,
        }

    except Exception as e:
        print(f"  Feed-Parsing fehlgeschlagen: {e} - nutze Podcast-Beschreibung")
        return {
            "episode_titel": "",
            "episode_beschreibung": podcast.get("beschreibung", ""),
        }

# ─── Schritt 3: Zusammenfassung mit Gemini generieren ────────────────────────

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
            "Fasse in 2-3 Saetzen zusammen, worum es in dieser konkreten Episode geht. "
            "Nenne den Episodentitel und erklaere den Inhalt so, dass Hoerer neugierig werden."
        )
    else:
        kontext = (
            f"Podcast: {podcast['name']} von {podcast['autor']}\n"
            f"Beschreibung: {episode['episode_beschreibung']}"
        )
        aufgabe = (
            "Fasse in 2-3 Saetzen zusammen, worum es in diesem Podcast generell geht. "
            "Erklaere das Konzept so, dass Hoerer neugierig werden."
        )

    prompt = (
        "Du bist eine freundliche Podcast-Moderatorin. "
        "Schreibe einen kurzen Hoertext (max. 80 Woerter, ca. 30 Sekunden) auf Deutsch.\n\n"
        f"{kontext}\n\n"
        f"{aufgabe}\n\n"
        "Regeln:\n"
        "- Starte direkt mit dem Podcast- oder Episodennamen\n"
        "- Kein Hinweis auf Links oder Shownotes\n"
        "- Keine Sonderzeichen oder Aufzaehlungen\n"
        "- Locker, natuerlich, gesprochen klingend\n\n"
        "Antworte NUR mit dem Sprechtext.\n\n"
        "Gib danach in einer neuen Zeile 'EMPFEHLUNG: [Podcast-Name]' an."
    )

    # Retry-Logik: bis zu 5 Versuche bei 503
    for versuch in range(5):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            text = response.text.strip()

            zeilen = text.split("\n")
            skript_zeilen = []
            empfohlener_podcast = podcast["name"]

            for zeile in zeilen:
                if zeile.startswith("EMPFEHLUNG:"):
                    empfohlener_podcast = zeile.replace("EMPFEHLUNG:", "").strip()
                else:
                    skript_zeilen.append(zeile)

            return {
                "skript": "\n".join(skript_zeilen).strip(),
                "empfohlener_podcast": empfohlener_podcast,
                "empfohlener_link": podcast["link"],
                "episode_titel": episode.get("episode_titel", ""),
            }

        except Exception as e:
            wartezeit = 10 * (versuch + 1)
            print(f"  Gemini Fehler (Versuch {versuch+1}/5): {e}")
            print(f"  Warte {wartezeit} Sekunden...")
            time.sleep(wartezeit)

    print("  Alle Versuche fehlgeschlagen - nutze Fallback")
    return {
        "skript": f"{podcast['name']} ist der aktuelle Top-Podcast in der Kategorie {kategorie_name}.",
        "empfohlener_podcast": podcast["name"],
        "empfohlener_link": podcast["link"],
        "episode_titel": "",
    }

# ─── Schritt 4: Audio mit ElevenLabs generieren ──────────────────────────────

def generiere_audio(skript: str, dateiname: str) -> bool:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": os.environ["ELEVENLABS_API_KEY"]
    }
    data = {
        "text": skript,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8
        }
    }

    try:
        response = requests.post(url, json=data, headers=headers, timeout=30)
        response.raise_for_status()

        os.makedirs("audio", exist_ok=True)
        with open(f"audio/{dateiname}", "wb") as f:
            f.write(response.content)

        print(f"Audio gespeichert: audio/{dateiname}")
        return True
    except Exception as e:
        print(f"Fehler bei ElevenLabs: {e}")
        return False

# ─── Schritt 5: RSS Feed aktualisieren ───────────────────────────────────────

def aktualisiere_rss_feed(episoden: list):
    feed_pfad = "feed.xml"

    rss_string = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{PODCAST_NAME}</title>
    <description>{PODCAST_BESCHREIBUNG}</description>
    <link>{GITHUB_PAGES_URL}</link>
    <language>de</language>
    <itunes:author>Podcast Entdeckungen</itunes:author>
    <itunes:owner>
      <itunes:name>Podcast Entdeckungen</itunes:name>
      <itunes:email>victoria@frcollective.at</itunes:email>
    </itunes:owner>
    <itunes:category text="Society &amp; Culture"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{GITHUB_PAGES_URL}/cover.jpg"/>
"""

    alte_episoden = []
    if os.path.exists(feed_pfad):
        try:
            tree = ET.parse(feed_pfad)
            root = tree.getroot()
            channel = root.find("channel")
            if channel:
                heute = datetime.now().strftime("%d.%m.%Y")
                for item in channel.findall("item"):
                    title = item.findtext("title", "")
                    if heute not in title:
                        alte_episoden.append(ET.tostring(item, encoding="unicode"))
        except Exception:
            pass

    for ep in episoden:
        datum_rfc = formatdate(ep["timestamp"])
        # Texte XML-sicher machen
        titel_sicher = xml_sicher(ep["titel"])
        skript_sicher = xml_sicher(ep["beschreibung"])
        podcast_name_sicher = xml_sicher(ep["podcast_name"])
        episode_titel_sicher = xml_sicher(ep.get("episode_titel", ""))
        episode_info = f"Episode: {episode_titel_sicher}\n\n" if episode_titel_sicher else ""

        rss_string += f"""
    <item>
      <title>{titel_sicher}</title>
      <description><![CDATA[{episode_info}{ep["beschreibung"]}

Zum Podcast: <a href="{ep["podcast_link"]}">{podcast_name_sicher}</a>

Erstellt von Podcast Entdeckungen. Alle Rechte am empfohlenen Podcast liegen beim jeweiligen Urheber.]]></description>
      <enclosure url="{GITHUB_PAGES_URL}/audio/{ep["dateiname"]}" type="audio/mpeg" length="0"/>
      <guid isPermaLink="false">{ep["guid"]}</guid>
      <pubDate>{datum_rfc}</pubDate>
      <itunes:duration>30</itunes:duration>
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

# ─── Schritt 6: Website aktualisieren ────────────────────────────────────────

def aktualisiere_website(episoden: list):
    heute = datetime.now().strftime("%d.%m.%Y")

    episoden_html = ""
    for ep in episoden:
        episode_zeile = f'<p class="episode-titel">Neueste Episode: {ep["episode_titel"]}</p>' if ep.get("episode_titel") else ""
        episoden_html += f"""
        <div class="episode-card">
          <div class="kategorie-badge">{ep['kategorie']}</div>
          <h2>{ep['podcast_name']}</h2>
          {episode_zeile}
          <audio controls>
            <source src="audio/{ep['dateiname']}" type="audio/mpeg">
          </audio>
          <p><a href="{ep['podcast_link']}" target="_blank">Zum Podcast</a></p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Podcast Entdeckungen - Die besten deutschen Podcasts taeglich</title>
  <meta name="description" content="Taeglich die besten deutschsprachigen Podcasts entdecken. True Crime, Business und Nachrichten.">
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
  <p class="subtitle">Taeglich die besten deutschsprachigen Podcasts - kuratiert mit KI</p>
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

    for kategorie_id, kategorie in KATEGORIEN.items():
        print(f"\nVerarbeite Kategorie: {kategorie['name']}")

        podcasts = hole_top_podcasts(kategorie["itunes_genre_id"])
        if not podcasts:
            print(f"  Keine Podcasts gefunden fuer {kategorie['name']}")
            continue

        podcast = podcasts[0]
        print(f"  Top-Podcast: {podcast['name']}")

        episode = hole_neueste_episode(podcast)
        ergebnis = generiere_skript(kategorie["name"], podcast, episode)
        print(f"  Skript generiert ({len(ergebnis['skript'])} Zeichen)")

        dateiname = f"{heute}-{kategorie_id}.mp3"
        audio_ok = generiere_audio(ergebnis["skript"], dateiname)

        if audio_ok:
            titel = f"{kategorie['name']}: {ergebnis['empfohlener_podcast']}"
            if ergebnis.get("episode_titel"):
                titel += f" - {ergebnis['episode_titel'][:50]}"

            episoden.append({
                "titel": titel,
                "beschreibung": ergebnis["skript"],
                "dateiname": dateiname,
                "podcast_name": ergebnis["empfohlener_podcast"],
                "podcast_link": ergebnis["empfohlener_link"],
                "episode_titel": ergebnis.get("episode_titel", ""),
                "kategorie": kategorie["name"],
                "guid": f"{heute}-{kategorie_id}",
                "timestamp": time.time()
            })

        time.sleep(2)

    if episoden:
        aktualisiere_rss_feed(episoden)
        aktualisiere_website(episoden)
        print(f"\nFertig! {len(episoden)} Episoden generiert.")
    else:
        print("\nKeine Episoden generiert.")

if __name__ == "__main__":
    main()
