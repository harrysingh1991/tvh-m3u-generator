import os
import requests
from flask import Flask, send_file

app = Flask(__name__)

# Load config from environment
TVH_URL = os.environ.get("TVH_URL", "http://localhost:9981")
TVH_USER = os.environ.get("TVH_USERNAME", "admin")
TVH_PASS = os.environ.get("TVH_PASSWORD", "admin")
TVH_PROFILE = os.environ.get("TVH_PROFILE", "pass")
M3U_FILENAME = os.environ.get("M3U_FILENAME", "playlist.m3u")

session = requests.Session()
session.auth = (TVH_USER, TVH_PASS)

def fetch_channels():
    channels_url = f"{TVH_URL}/api/channel/grid?limit=99999"
    tags_url = f"{TVH_URL}/api/channel/tag/grid?limit=99999"

    channels = session.get(channels_url).json().get("entries", [])
    tags = session.get(tags_url).json().get("entries", [])

    tag_lookup = {tag['uuid']: tag['name'] for tag in tags}
    return channels, tag_lookup

def write_m3u():
    channels, tag_lookup = fetch_channels()
    lines = ['#EXTM3U']

    for ch in sorted(channels, key=lambda x: x.get("number", 99999)):
        name = ch.get("name", "Unknown")
        number = ch.get("number", 0)
        uuid = ch.get("uuid")
        tags = ch.get("tags", [])
        group = tag_lookup.get(tags[0], "TV") if tags else "TV"

        url = f"{TVH_URL}/stream/channel/{uuid}?profile={TVH_PROFILE}"
        url = url.replace("://", f"://{TVH_USER}:{TVH_PASS}@")  # inject auth

        lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{name}" tvg-logo="" group-title="{group}",{name}')
        lines.append(url)

    with open(M3U_FILENAME, "w") as f:
        f.write("\n".join(lines))

@app.route("/")
def playlist():
    write_m3u()
    return send_file(M3U_FILENAME, mimetype="application/x-mpegURL")

if __name__ == "__main__":
    # Listen on fixed port 8080
    app.run(host="0.0.0.0", port=8080)
