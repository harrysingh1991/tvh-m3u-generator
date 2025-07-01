import os
import time
import logging
import requests
from flask import Flask, Response, render_template_string
import re
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Load config from environment variables with defaults for dev
TVH_HOST = os.getenv("TVH_HOST", "127.0.0.1")
TVH_PORT = int(os.getenv("TVH_PORT", 9981))
TVH_PERSISTENT_PASS = os.getenv("TVH_PERSISTENT_PASS", "")
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", 600))  # seconds, default 10 minutes
SERVER_PORT = int(os.getenv("SERVER_PORT", 9985))  # <-- Added server port variable

base_url = f"http://{TVH_HOST}:{TVH_PORT}"

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# Cache variables
cached_playlist = None
last_refresh_time = 0

def url_with_auth(path: str) -> str:
    separator = '&' if '?' in path else '?'
    return f"{base_url}{path}{separator}auth={TVH_PERSISTENT_PASS}"

def fetch_tags():
    tags_url = url_with_auth("/playlist/tags")
    logging.info(f"Fetching tags from: {tags_url}")
    resp = requests.get(tags_url)
    resp.raise_for_status()
    lines = resp.text.splitlines()

    tags = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            name = line.split(',', 1)[1].strip()
            i += 1
            if i < len(lines):
                url = lines[i].strip()
                match = re.search(r'/tagid/(\d+)', url)
                if match:
                    tag_id = match.group(1)
                    tags.append({"name": name, "tag_id": tag_id})
        i += 1
    logging.info(f"Found {len(tags)} tags")
    return tags

def fetch_channels_for_tag(tag_id):
    path = f"/playlist/tagid/{tag_id}?profile=pass"
    full_url = url_with_auth(path)
    logging.info(f"Fetching channels for tag {tag_id} from: {full_url}")
    resp = requests.get(full_url)
    resp.raise_for_status()
    return resp.text

def inject_group_titles_and_auth(m3u_text, group_name):
    lines = m3u_text.splitlines()
    updated_lines = []
    for line in lines:
        if line.startswith("#EXTINF"):
            if 'group-title' not in line:
                line = line.replace(",", f' group-title="{group_name}",', 1)
            updated_lines.append(line)
        elif line.startswith("http"):
            separator = '&' if '?' in line else '?'
            if "auth=" not in line:
                line += f"{separator}auth={TVH_PERSISTENT_PASS}"
            updated_lines.append(line)
        else:
            updated_lines.append(line)
    return '\n'.join(updated_lines) + "\n"

@app.route("/")
def index():
    m3u_url = "/playlist.m3u"
    epg_url = "/epg.xml"  # We can add this endpoint if you want (dummy for now)
    html = f"""
    <html>
    <head><title>TVHeadend Playlist Server</title></head>
    <body>
        <h1>TVHeadend Playlist Server</h1>
        <ul>
            <li><a href="{m3u_url}">Download M3U Playlist</a></li>
            <li><a href="{epg_url}">Download EPG XML</a></li>
        </ul>
        <p>Playlist auto-refresh interval: {REFRESH_INTERVAL} seconds</p>
    </body>
    </html>
    """
    return html

@app.route("/playlist.m3u")
def playlist():
    global cached_playlist, last_refresh_time

    current_time = time.time()
    if cached_playlist and (current_time - last_refresh_time) < REFRESH_INTERVAL:
        logging.info("Serving cached playlist")
        return Response(cached_playlist, mimetype="application/x-mpegurl")

    try:
        tags = fetch_tags()
    except Exception as e:
        logging.error(f"Failed to fetch playlist tags: {e}")
        return f"Failed to fetch playlist tags: {e}", 500

    combined_playlist = "#EXTM3U\n"
    for tag in tags:
        tag_id = tag["tag_id"]
        group_name = tag["name"]
        try:
            m3u_text = fetch_channels_for_tag(tag_id)
            m3u_for_tag = inject_group_titles_and_auth(m3u_text, group_name)
            combined_playlist += m3u_for_tag
        except Exception as e:
            logging.error(f"Failed to fetch channels for tag {group_name}: {e}")
            combined_playlist += f"# Failed to fetch channels for tag {group_name}: {e}\n"

    cached_playlist = combined_playlist
    last_refresh_time = current_time
    logging.info(f"Generated combined playlist for {len(tags)} tags")

    return Response(combined_playlist, mimetype="application/x-mpegurl")

@app.route("/epg.xml")
def epg():
    try:
        epg_url = url_with_auth("/xmltv/channels")
        logging.info(f"Proxying EPG from: {epg_url}")
        resp = requests.get(epg_url)
        resp.raise_for_status()
        return Response(resp.content, mimetype="application/xml")
    except Exception as e:
        logging.error(f"Failed to fetch EPG XML: {e}")
        return f"Failed to fetch EPG XML: {e}", 500

if __name__ == "__main__":
    logging.info(f"Starting TVHeadend Playlist Server on port {SERVER_PORT}")
    app.run(host="0.0.0.0", port=SERVER_PORT)
