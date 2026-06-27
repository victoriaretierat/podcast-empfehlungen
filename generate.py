import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate
import time
import google.generativeai as genai

# ─── Konfiguration ───────────────────────────────────────────────────────────

KATEGORIEN = {
    "true-crime": {
        "name": "True Crime",
        "itunes_genre_id": "1488",
        "beschreibung": "Wahre Kriminalfälle, Mysteries und Cold Cases"
    },
    "business": {
        "name": "Business",
        "itunes_genre_id": "1321",
        "beschreibung": "Wirtschaft, Unternehmertum und Karriere"
    },
    "nachrichten": {
        "name": "Nachrichten",
        "itunes_genre_id": "1526",
        "beschreibung": "Aktuelle Nachrichten und Politik"
    }
}

PODCAST_NAME = "Podcast Entdeckungen"
PODCAST_BESCHREIBUNG = "Täglich die besten deutschsprachigen Podcasts entdecken – True Crime, Business und Nachrichten."
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "http://localhost")

# Bella – natürliche weibliche Stimme
ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"

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
                "bild": eintrag.get("im:image", [{}])[-1].get("label", ""),
            })
        return podcasts
    except Exception as e:
        print(f"Fehler beim Laden der iTunes Charts: {e}")
        return []

# ─── Schritt 2: Skript mit Gemini generieren ─────────────────────────────────

def generiere_skript(kategorie_name: str, podcasts: list) -> dict:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = (
        "Du bist eine freundliche Podcast-Empfehlungs-Moderatorin. "
        "Schreibe einen kurzen Hörtext (max. 80 Wörter, ca. 30 Sekunden) auf Deutsch.\n\n"
        f"Kategorie: {kategorie_name}\n"
        f"Aktueller Top-Podcast laut iTunes Deutschland: {podcasts[0]['name']} von {podcasts[0]['autor']}\n"
        f"Beschreibung: {podcasts[0]['beschreibung'][:300]}\n\n"
        "Der Text soll:\n"
        "- Direkt mit dem Podcast-Namen einsteigen, keine lange Begrüßung\n"
        "- In 2-3 Sätzen erklären worum es in diesem Podcast geht\n"
        "- Neugierig machen, locker und natürlich klingen\n"
        "- Keine Sonderzeichen, keine Aufzählungen, kein Hinweis auf Shownotes oder Links\n\n"
        "Antworte NUR mit dem Sprechtext, ohne Anführungszeichen oder Formatierung.\n\n"
        "Außerdem: Gib am Ende in einer neuen Zeile 'EMPFEHLUNG: [Podcast-Name]' an."
    )

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()

        zeilen = text.split("\n")
        skript_zeilen = []
        empfohlener_podcast = podcasts[0]["name"] if podcasts else ""

        for zeile in zeilen:
            if zeile.startswith("EMPFEHLUNG:"):
                empfohlener_podcast = zeile.replace("EMPFEHLUNG:", "").strip()
            else:
                skript_zeilen.append(zeile)

        return {
            "skript": "\n".join(skript_zeilen).strip(),
            "empfohlener_podcast": empfohlener_podcast,
            "empfohlener_link": podcasts[0]["link"] if podcasts else ""
        }
    except Exception as e:
        print(f"Fehler bei Gemini: {e}")
        return {
            "skript": f"{podcasts[0]['name'] if podcasts else 'Dieser Podcast'} ist der aktuelle Top-Podcast in der Kategorie {kategorie_name}.",
            "empfohlener_podcast": podcasts[0]["name"] if podcasts else "",
            "empfohlener_link": podcasts[0]["link"] if podcasts else ""
        }

# ─── Schritt 3: Audio mit ElevenLabs generieren ──────────────────────────────

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

        print(f"✅ Audio gespeichert: audio/{dateiname}")
        return True
    except Exception as e:
        print(f"❌ Fehler bei ElevenLabs: {e}")
        return False

# ─── Schritt 4: RSS Feed aktualisieren ───────────────────────────────────────

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
        rss_string += f"""
    <item>
      <title>{ep["titel"]}</title>
      <description><![CDATA[{ep["beschreibung"]}

🎧 Empfohlener Podcast: <a href="{ep["podcast_link"]}">{ep["podcast_name"]}</a>

Dieser Beitrag wurde automatisch erstellt von Podcast Entdeckungen. Alle Rechte an den empfohlenen Podcasts liegen bei den jeweiligen Urhebern.]]></description>
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

    print(f"✅ RSS Feed aktualisiert mit {len(episoden)} neuen Episoden")

# ─── Schritt 5: Website aktualisieren ────────────────────────────────────────

def aktualisiere_website(episoden: list):
    heute = datetime.now().strftime("%d.%m.%Y")

    episoden_html = ""
    for ep in episoden:
        episoden_html += f"""
        <div class="episode-card">
          <div class="kategorie-badge">{ep['kategorie']}</div>
          <h2>{ep['titel']}</h2>
          <audio controls>
            <source src="audio/{ep['dateiname']}" type="audio/mpeg">
          </audio>
          <p>🎧 Empfohlen: <a href="{ep['podcast_link']}" target="_blank">{ep['podcast_name']}</a></p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Podcast Entdeckungen – Die besten deutschen Podcasts täglich</title>
  <meta name="description" content="Täglich die besten deutschsprachigen Podcasts entdecken. True Crime, Business und Nachrichten – kuratiert und empfohlen.">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; color: #333; }}
    h1 {{ color: #1a1a2e; font-size: 2em; }}
    .subtitle {{ color: #666; margin-top: -10px; }}
    .episode-card {{ background: white; border-radius: 12px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .kategorie-badge {{ display: inline-block; background: #1a1a2e; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.8em; margin-bottom: 10px; }}
    h2 {{ margin: 8px 0; font-size: 1.3em; }}
    audio {{ width: 100%; margin: 12px 0; }}
    a {{ color: #e94560; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .rss-link {{ background: #f8a500; color: white; padding: 10px 20px; border-radius: 8px; display: inline-block; margin-top: 20px; }}
    footer {{ text-align: center; color: #999; margin-top: 40px; font-size: 0.85em; }}
  </style>
</head>
<body>
  <h1>🎙️ Podcast Entdeckungen</h1>
  <p class="subtitle">Täglich die besten deutschsprachigen Podcasts – kuratiert mit KI</p>
  <p><strong>Heute, {heute}:</strong></p>
  {episoden_html}
  <p><a href="feed.xml" class="rss-link">📡 RSS Feed abonnieren</a></p>
  <footer>
    Podcast Entdeckungen – Automatisch generiert. Alle empfohlenen Podcasts sind Eigentum ihrer jeweiligen Urheber.
  </footer>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ Website aktualisiert")

# ─── Hauptprogramm ───────────────────────────────────────────────────────────

def main():
    print(f"🚀 Starte Generierung – {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    heute = datetime.now().strftime("%Y-%m-%d")
    episoden = []

    for kategorie_id, kategorie in KATEGORIEN.items():
        print(f"\n📂 Verarbeite Kategorie: {kategorie['name']}")

        podcasts = hole_top_podcasts(kategorie["itunes_genre_id"])
        if not podcasts:
            print(f"  ⚠️ Keine Podcasts gefunden für {kategorie['name']}")
            continue
        print(f"  ✅ {len(podcasts)} Podcasts geladen, Top: {podcasts[0]['name']}")

        ergebnis = generiere_skript(kategorie["name"], podcasts)
        print(f"  ✅ Skript generiert ({len(ergebnis['skript'])} Zeichen)")

        dateiname = f"{heute}-{kategorie_id}.mp3"
        audio_ok = generiere_audio(ergebnis["skript"], dateiname)

        if audio_ok:
            episoden.append({
                "titel": f"{kategorie['name']}: {ergebnis['empfohlener_podcast']} – {datetime.now().strftime('%d.%m.%Y')}",
                "beschreibung": ergebnis["skript"],
                "dateiname": dateiname,
                "podcast_name": ergebnis["empfohlener_podcast"],
                "podcast_link": ergebnis["empfohlener_link"],
                "kategorie": kategorie["name"],
                "guid": f"{heute}-{kategorie_id}",
                "timestamp": time.time()
            })

        time.sleep(2)

    if episoden:
        aktualisiere_rss_feed(episoden)
        aktualisiere_website(episoden)
        print(f"\n🎉 Fertig! {len(episoden)} Episoden generiert.")
    else:
        print("\n❌ Keine Episoden generiert.")

if __name__ == "__main__":
    main()
