import os
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate
import time
import google.generativeai as genai

# ─── Konfiguration ───────────────────────────────────────────────────────────

KATEGORIEN = {
    "true-crime": {
        "name": "True Crime",
        "itunes_genre_id": "1488",   # True Crime Genre auf iTunes
        "beschreibung": "Wahre Kriminalfälle, Mysteries und Cold Cases"
    },
    "business": {
        "name": "Business",
        "itunes_genre_id": "1321",   # Business Genre auf iTunes
        "beschreibung": "Wirtschaft, Unternehmertum und Karriere"
    },
    "nachrichten": {
        "name": "Nachrichten",
        "itunes_genre_id": "1526",   # News Genre auf iTunes
        "beschreibung": "Aktuelle Nachrichten und Politik"
    }
}

PODCAST_NAME = "Podcast Entdeckungen"
PODCAST_BESCHREIBUNG = "Täglich die besten deutschsprachigen Podcasts entdecken – True Crime, Business und Nachrichten."
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "http://localhost")

# ElevenLabs Voice ID – "Antoni" (klingt natürlich auf Deutsch)
# Andere kostenlose Stimmen: "Rachel", "Domi", "Bella"
ELEVENLABS_VOICE_ID = "ErXwobaYiN019PkySvjV"

# ─── Schritt 1: Top Podcasts von iTunes holen ────────────────────────────────

def hole_top_podcasts(genre_id: str, land: str = "de") -> list:
    """Holt Top 10 Podcasts für ein Genre aus dem iTunes Chart."""
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

# ─── Schritt 2: Empfehlungs-Skript mit Gemini generieren ─────────────────────

def generiere_skript(kategorie_name: str, podcasts: list) -> dict:
    """Generiert ein Empfehlungs-Skript mit Gemini AI."""
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")

    # Podcast-Liste für den Prompt vorbereiten
    podcast_liste = "\n".join([
        f"{i+1}. {p['name']} von {p['autor']}"
        for i, p in enumerate(podcasts[:5])
    ])

    prompt = f"""Du bist ein freundlicher Podcast-Empfehlungs-Host. 
Schreibe ein kurzes Hör-Skript (max. 200 Wörter, ca. 60 Sekunden) auf Deutsch.

Kategorie: {kategorie_name}
Aktuelle Top-Podcasts laut iTunes Deutschland:
{podcast_liste}

Das Skript soll:
- Mit einer kurzen Begrüßung starten: "Willkommen bei Podcast Entdeckungen!"
- Den Podcast auf Platz 1 empfehlen und kurz erklären warum er interessant ist
- Mit dem Hinweis enden: "Den Link zum Podcast findest du in den Shownotes."
- Natürlich und gesprochen klingen (keine Aufzählungspunkte, keine Sonderzeichen)
- Freundlich und enthusiastisch sein

Antworte NUR mit dem Skript-Text, ohne Anführungszeichen oder Formatierung.

Außerdem: Gib am Ende in einer neuen Zeile "EMPFEHLUNG: [Podcast-Name]" an."""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Empfehlung extrahieren
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
            "skript": f"Willkommen bei Podcast Entdeckungen! Heute empfehlen wir dir {podcasts[0]['name'] if podcasts else 'einen tollen Podcast'} aus der Kategorie {kategorie_name}. Den Link findest du in den Shownotes!",
            "empfohlener_podcast": podcasts[0]["name"] if podcasts else "",
            "empfohlener_link": podcasts[0]["link"] if podcasts else ""
        }

# ─── Schritt 3: Audio mit ElevenLabs generieren ──────────────────────────────

def generiere_audio(skript: str, dateiname: str) -> bool:
    """Konvertiert Text zu Sprache mit ElevenLabs und speichert als MP3."""
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

# ─── Schritt 4: RSS Feed generieren/aktualisieren ────────────────────────────

def aktualisiere_rss_feed(episoden: list):
    """Erstellt oder aktualisiert den RSS Feed (Podcast-Format)."""
    
    # Bestehenden Feed laden oder neu erstellen
    feed_pfad = "feed.xml"
    
    # Namespace für iTunes-Podcast-Felder
    ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
    ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
    
    # Neuen RSS Feed aufbauen
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
    
    # Bestehende Episoden aus altem Feed laden
    alte_episoden = []
    if os.path.exists(feed_pfad):
        try:
            tree = ET.parse(feed_pfad)
            root = tree.getroot()
            channel = root.find("channel")
            if channel:
                for item in channel.findall("item"):
                    title = item.findtext("title", "")
                    # Heute's Episoden überspringen (werden neu generiert)
                    heute = datetime.now().strftime("%d.%m.%Y")
                    if heute not in title:
                        alte_episoden.append(ET.tostring(item, encoding="unicode"))
        except:
            pass
    
    # Neue Episoden hinzufügen
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
      <itunes:duration>60</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
    
    # Alte Episoden anhängen (max. 30 behalten)
    for alte_ep in alte_episoden[:27]:  # 27 alte + 3 neue = 30 gesamt
        rss_string += f"\n    {alte_ep}"
    
    rss_string += """
  </channel>
</rss>"""
    
    with open(feed_pfad, "w", encoding="utf-8") as f:
        f.write(rss_string)
    
    print(f"✅ RSS Feed aktualisiert mit {len(episoden)} neuen Episoden")

# ─── Schritt 5: Website aktualisieren ────────────────────────────────────────

def aktualisiere_website(episoden: list):
    """Aktualisiert die index.html mit den heutigen Empfehlungen."""
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
        
        # 1. Top Podcasts holen
        podcasts = hole_top_podcasts(kategorie["itunes_genre_id"])
        if not podcasts:
            print(f"  ⚠️ Keine Podcasts gefunden für {kategorie['name']}")
            continue
        print(f"  ✅ {len(podcasts)} Podcasts geladen, Top: {podcasts[0]['name']}")
        
        # 2. Skript generieren
        ergebnis = generiere_skript(kategorie["name"], podcasts)
        print(f"  ✅ Skript generiert ({len(ergebnis['skript'])} Zeichen)")
        
        # 3. Audio generieren
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
        
        # Kurze Pause zwischen API-Calls
        time.sleep(2)
    
    # 4. RSS Feed und Website aktualisieren
    if episoden:
        aktualisiere_rss_feed(episoden)
        aktualisiere_website(episoden)
        print(f"\n🎉 Fertig! {len(episoden)} Episoden generiert.")
    else:
        print("\n❌ Keine Episoden generiert.")

if __name__ == "__main__":
    main()
