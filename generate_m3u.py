import os
import time
import logging
import requests
from flask import Flask, Response
import re
from dotenv import load_dotenv
import urllib.parse

# Load environment variables from a .env file (if present)
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Read required environment variables with defaults
TVH_HOST = os.getenv("TVH_HOST", "127.0.0.1")  # TVHeadend server IP
TVH_PORT = int(os.getenv("TVH_PORT", 9981))   # TVHeadend server port
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", 600))  # Playlist cache duration in seconds
SERVER_PORT = int(os.getenv("SERVER_PORT", 9987))  # Flask server port
USER_CREDENTIALS = os.getenv("TVH_USERS", "")  # Format: user1:pass1,user2:pass2,...

# Construct base URL for all TVH requests
base_url = f"http://{TVH_HOST}:{TVH_PORT}"

# Configure basic logging for visibility
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# Global cache for the combined M3U playlist
cached_playlist = None
last_refresh_time = 0

# Parse the comma-separated user credentials into a list of dicts
def parse_users(creds_str):
    pairs = [u.strip() for u in creds_str.split(",") if ":" in u]
    return [{"user": u.split(":")[0], "pass": u.split(":")[1]} for u in pairs]

# Load user accounts (TVH persistent users with passwords)
USERS = parse_users(USER_CREDENTIALS)

# Build a TVHeadend URL with persistent password appended as a query param
def url_with_auth(path: str, user_pass: str) -> str:
    separator = '&' if '?' in path else '?'
    return f"{base_url}{path}{separator}auth={user_pass}"

# Fetch all available tags (channel groups) visible to a given user
def fetch_tags(user_pass):
    tags_url = url_with_auth("/playlist/tags", user_pass)
    logging.info(f"Fetching tags from: {tags_url}")
    resp = requests.get(tags_url)
    resp.raise_for_status()
    lines = resp.text.splitlines()

    tags = []
    i = 0
    # Process the M3U format line-by-line to extract tag IDs and names
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            name = line.split(',', 1)[1].strip()  # Extract group name
            i += 1
            if i < len(lines):
                url = lines[i].strip()
                match = re.search(r'/tagid/(\d+)', url)
                if match:
                    tag_id = match.group(1)
                    tags.append({"name": name, "tag_id": tag_id})
        i += 1
    return tags

# Fetch channels for a specific tag ID; profile omitted to let TVH pick allowed profile
def fetch_channels_for_tag(tag_id, user_pass):
    path = f"/playlist/tagid/{tag_id}"
    full_url = url_with_auth(path, user_pass)
    logging.info(f"Fetching channels for tag {tag_id} from: {full_url}")
    resp = requests.get(full_url)
    resp.raise_for_status()
    return resp.text

# Inject group-title attribute into #EXTINF lines and append auth token to stream URLs
def inject_group_titles_and_auth(m3u_text, group_name, user_pass):
    lines = m3u_text.splitlines()
    updated_lines = []
    for line in lines:
        if line.startswith("#EXTINF"):
            if 'group-title' not in line:
                # Inject group-title just after the comma in #EXTINF line
                line = line.replace(",", f' group-title="{group_name}",', 1)
            updated_lines.append(line)
        elif line.startswith("http"):
            # Remove any profile= param and add auth param
            parsed = urllib.parse.urlparse(line)
            query = urllib.parse.parse_qs(parsed.query)
            query.pop("profile", None)  # Remove profile param if present
            if "auth" not in query:
                query["auth"] = [user_pass]
            new_query = urllib.parse.urlencode(query, doseq=True)
            new_url = urllib.parse.urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            ))
            updated_lines.append(new_url)
        else:
            updated_lines.append(line)
    return '\n'.join(updated_lines) + "\n"

# Main index page showing download links and info
@app.route("/")
def index():
    html = f"""
    <html>
    <head><title>TVHeadend Playlist Server</title></head>
    <body>
        <h1>TVHeadend Playlist Server</h1>
        <ul>
            <li><a href="/playlist.m3u">Download M3U Playlist</a></li>
            <li><a href="/epg.xml">Download EPG XML</a></li>
        </ul>
        <p>Playlist auto-refresh interval: {REFRESH_INTERVAL} seconds</p>
    </body>
    </html>
    """
    return html

# Playlist endpoint that merges all user-visible channels
@app.route("/playlist.m3u")
def playlist():
    global cached_playlist, last_refresh_time
    current_time = time.time()

    # Serve cached playlist if it is still fresh
    if cached_playlist and (current_time - last_refresh_time) < REFRESH_INTERVAL:
        logging.info("Serving cached playlist")
        return Response(cached_playlist, mimetype="application/x-mpegurl")

    combined_playlist = "#EXTM3U\n"  # M3U header

    # Loop through each user and collect channels they can access
    for user in USERS:
        user_pass = user["pass"]
        try:
            tags = fetch_tags(user_pass)
        except Exception as e:
            logging.error(f"Failed to fetch tags for user {user['user']}: {e}")
            combined_playlist += f"# Failed to fetch tags for user {user['user']}: {e}\n"
            continue

        # Fetch channels for each tag available to that user
        for tag in tags:
            tag_id = tag["tag_id"]
            tag_name = tag["name"]
            try:
                m3u_text = fetch_channels_for_tag(tag_id, user_pass)
                # Inject group-title and append auth token per user/tag
                m3u_with_injections = inject_group_titles_and_auth(m3u_text, tag_name, user_pass)
                combined_playlist += m3u_with_injections
            except Exception as e:
                logging.error(f"Failed to fetch tag {tag_id} for user {user['user']}: {e}")
                combined_playlist += f"# Failed tag {tag_id} for user {user['user']}: {e}\n"

    # Update cache
    cached_playlist = combined_playlist
    last_refresh_time = current_time
    logging.info("Generated updated playlist")

    return Response(combined_playlist, mimetype="application/x-mpegurl")

# EPG endpoint — proxies XMLTV from TVHeadend using the first user
@app.route("/epg.xml")
def epg():
    try:
        if not USERS:
            raise Exception("No TVH_USERS defined.")
        epg_url = url_with_auth("/xmltv/channels", USERS[0]["pass"])
        logging.info(f"Proxying EPG from: {epg_url}")
        resp = requests.get(epg_url)
        resp.raise_for_status()
        return Response(resp.content, mimetype="application/xml")
    except Exception as e:
        logging.error(f"Failed to fetch EPG XML: {e}")
        return f"Failed to fetch EPG XML: {e}", 500

# Entry point for the Flask server
if __name__ == "__main__":
    logging.info(f"Starting TVHeadend Playlist Server on port {SERVER_PORT}")
    app.run(host="0.0.0.0", port=SERVER_PORT)
