import logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

import eventlet
eventlet.monkey_patch()
import os
import time
import requests
from flask import Flask, Response, redirect, url_for, request, render_template
import re
import urllib.parse
import datetime
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
import threading

# Load environment variables
load_dotenv()

# Consolidated environment variable check
def check_and_log_env_vars():
    required_vars = [
        "TVH_HOST",
        "TVH_PORT",
        "REFRESH_INTERVAL",
        "TVH_USERS",
        "TVH_EPG_AUTH"
    ]
    missing_vars = []
    for var in required_vars:
        value = os.getenv(var)
        if value is None or value == "":
            logging.error(f"Missing required environment variable: {var}")
            missing_vars.append(var)
        else:
            logging.info(f"ENV CHECK: {var} is set to '{value}'")
    if missing_vars:
        logging.error(f"Exiting due to missing environment variables: {', '.join(missing_vars)}")
        exit(1)

check_and_log_env_vars()

# Read environment variables
TVH_HOST = os.getenv("TVH_HOST","127.0.0.1")
TVH_PORT = int(os.getenv("TVH_PORT","9981"))
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL","600"))
USER_CREDENTIALS = os.getenv("TVH_USERS")
TVH_EPG_AUTH = os.getenv("TVH_EPG_AUTH")
TVH_APPEND_ICON_AUTH = os.getenv("TVH_APPEND_ICON_AUTH", "0").lower() in ("1", "true", "yes")
EPG_STRIP_OFFSET = os.getenv("EPG_STRIP_OFFSET", "0").lower() in ("1", "true", "yes")

# Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

# Global variables
cached_playlist = None
last_refresh_time = 0
last_playlist_update = int(time.time())
SERVER_START_TIME = int(time.time())

# Helper variables and functions
base_url = f"http://{TVH_HOST}:{TVH_PORT}"

def parse_users(creds_str):
    logging.info("Parsing user credentials")
    pairs = [u.strip() for u in creds_str.split(",") if ":" in u]
    users = [{"user": u.split(":")[0], "pass": u.split(":")[1]} for u in pairs]
    logging.info(f"Parsed users: {[u['user'] for u in users]}")
    return users

# Parse user credentials from environment variable
USERS = parse_users(USER_CREDENTIALS)

def url_with_auth(path: str, user_pass: str) -> str:
    separator = '&' if '?' in path else '?'
    url = f"{base_url}{path}{separator}auth={user_pass}"
    logging.info(f"Generated URL with auth: {url}")
    return url

def fetch_tags(user_pass):
    tags_url = url_with_auth("/playlist/tags", user_pass)
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
                    logging.info(f"Found tag: {tag_id} ({name})")
                    tags.append({"name": name, "tag_id": tag_id})
        i += 1
    logging.info(f"Total tags fetched: {len(tags)}")
    return tags

def fetch_channels_for_tag(tag_id, user_pass):
    path = f"/playlist/tagid/{tag_id}"
    full_url = url_with_auth(path, user_pass)
    logging.info(f"Fetching channels for tag {tag_id} from: {full_url}")
    resp = requests.get(full_url)
    resp.raise_for_status()
    logging.info(f"Fetched channels for tag {tag_id}, response size: {len(resp.text)} bytes")
    return resp.text

def inject_group_titles(m3u_text, group_name):
    logging.info(f"Injecting group-title '{group_name}'")
    lines = m3u_text.splitlines()
    updated_lines = []
    for line in lines:
        if line.startswith("#EXTINF"):
            if 'group-title' not in line:
                line = line.replace(",", f' group-title="{group_name}",', 1)
        updated_lines.append(line)
    logging.info(f"Injected group-title for {group_name}")
    return '\n'.join(updated_lines) + "\n"

def inject_auth(m3u_text, user_pass):
    logging.info(f"Injecting auth for user")
    lines = m3u_text.splitlines()
    updated_lines = []
    for line in lines:
        if line.startswith("http"):
            parsed = urllib.parse.urlparse(line)
            query = urllib.parse.parse_qs(parsed.query)
            query.pop("profile", None)
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
    logging.info("Injected auth for user")
    return '\n'.join(updated_lines) + "\n"

def append_auth_to_tvg_logo(m3u_text, auth_token):
    logging.info("Checking if tvg-logo URLs need auth appended")
    def repl(match):
        url = match.group(1)
        if TVH_APPEND_ICON_AUTH:
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            if "auth" not in query:
                query["auth"] = [auth_token]
            new_query = urllib.parse.urlencode(query, doseq=True)
            new_url = urllib.parse.urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            ))
            logging.info(f"Appended auth to tvg-logo: {new_url}")
            return f'tvg-logo="{new_url}"'
        else:
            logging.info(f"No auth appended to tvg-logo: {url}")
            return f'tvg-logo="{url}"'
    pattern = r'tvg-logo="([^"]+)"'
    result = re.sub(pattern, repl, m3u_text)
    logging.info("Completed tvg-logo auth append check")
    return result

def parse_m3u_channels(m3u_text):
    logging.info("Parsing M3U channels")
    channels = []
    lines = m3u_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            group_title = re.search(r'group-title="([^"]+)"', line)
            tvg_id = re.search(r'tvg-id="([^"]+)"', line)
            tvg_logo = re.search(r'tvg-logo="([^"]+)"', line)
            channel_number = re.search(r'tvg-chno="([^"]+)"', line)
            channel_name = line.split(",", 1)[-1].strip()
            stream_url = lines[i+1] if i+1 < len(lines) else ""
            channelid_match = re.search(r'/channelid/(\d+)', stream_url)
            channelid = channelid_match.group(1) if channelid_match else ""
            channels.append({
                "group_title": group_title.group(1) if group_title else "",
                "channel_name": channel_name,
                "channel_number": channel_number.group(1) if channel_number else "",
                "tvg_id": tvg_id.group(1) if tvg_id else "",
                "tvg_logo": tvg_logo.group(1) if tvg_logo else "",
                "channelid": channelid,
                "stream_url": stream_url,
            })
            logging.info(f"Parsed channel: {channel_name} (ID: {channelid})")
            i += 2
        else:
            i += 1
    logging.info(f"Total channels parsed: {len(channels)}")
    return channels

# Flask endpoints

@app.route("/server_status")
def server_status():
    uptime = int(time.time()) - SERVER_START_TIME
    logging.info(f"Server status requested: uptime={uptime}s, last_playlist_update={last_playlist_update}")
    return {
        "start_time": SERVER_START_TIME,
        "uptime_seconds": uptime,
        "last_playlist_update": last_playlist_update
    }

@app.route("/refresh")
def refresh():
    threading.Thread(target=manual_refresh_playlist, daemon=True).start()
    logging.info("Manual playlist refresh triggered")
    return redirect(url_for('index'))

@app.route("/")
def index():
    client_ip = request.remote_addr
    user_agent = request.headers.get("User-Agent", "Unknown")
    logging.info(f"Index page rendered for client {client_ip} ({user_agent})")

    logging.info("Rendering main index page")
    if last_refresh_time:
        last_update_str = datetime.datetime.fromtimestamp(last_refresh_time).strftime("%Y-%m-%d %H:%M:%S")
    else:
        last_update_str = "Never"

    channel_rows = ""
    if cached_playlist:
        logging.info("Parsing cached playlist for channel table")
        channels = parse_m3u_channels(cached_playlist)
        for ch in channels:
            logo_html = ""
            if ch["tvg_logo"]:
                logo_html = f'<img src="{ch["tvg_logo"]}" alt="logo" style="height:32px;">'
            else:
                logging.info(f"No logo for channel: {ch['channel_name']}")
            copy_html = f'''
            <td style="text-align:center;">
                <button onclick="copyToClipboard('{ch["stream_url"]}')">Copy Link</button>
            </td>
            '''
            channel_rows += f"""
            <tr>
                <td>{ch["group_title"]}</td>
                <td>{ch["channel_name"]}</td>
                <td>{ch["channel_number"]}</td>
                <td>{ch["tvg_id"]}</td>
                <td>{ch.get("channelid", "")}</td>
                <td class="centered">{logo_html}</td>
                {copy_html}
            </tr>
            """
    else:
        logging.info("No cached playlist available for channel table")

    user_list_str = ", ".join([u["user"] for u in USERS])

    return render_template(
        "index.html",
        TVH_HOST=TVH_HOST,
        TVH_PORT=TVH_PORT,
        user_list_str=user_list_str,
        REFRESH_INTERVAL=REFRESH_INTERVAL,
        last_update_str=last_update_str,
        channel_rows=channel_rows
    )

@app.route("/playlist.m3u")
def playlist():
    global cached_playlist
    if not cached_playlist:
        return Response("#EXTM3U\n# Playlist is being generated, please try again in a moment.\n", mimetype="application/x-mpegurl")
    return Response(cached_playlist, mimetype="application/x-mpegurl")

@app.route("/epg.xml")
def epg():
    try:
        if not TVH_EPG_AUTH:
            raise Exception("TVH_EPG_AUTH is not set.")
        epg_url = f"http://{TVH_HOST}:{TVH_PORT}/xmltv/channels?auth={TVH_EPG_AUTH}"
        logging.info(f"Proxying full EPG from: {epg_url}")
        resp = requests.get(epg_url)
        resp.raise_for_status()
        epg_text = resp.text
        # Only replace if enabled
        if EPG_STRIP_OFFSET:
            logging.info("EPG_STRIP_OFFSET is enabled, replacing ' +0100\"' with '\"'")
            epg_text = epg_text.replace(' +0100"', '"')
        else:
            logging.info("EPG_STRIP_OFFSET is not enabled, leaving EPG XML unchanged")
        return Response(epg_text, mimetype="application/xml")
    except Exception as e:
        logging.error(f"Failed to fetch EPG XML: {e}")
        return f"Failed to fetch EPG XML: {e}", 500

def build_and_cache_playlist():
    global cached_playlist, last_refresh_time, last_playlist_update
    # Build and cache once immediately at startup
    logging.info("Initial build: Building and caching playlist")
    current_time = time.time()
    combined_playlist = "#EXTM3U\n"
    for user in USERS:
        user_pass = user["pass"]
        try:
            logging.info(f"Fetching tags for user: {user['user']}")
            tags = fetch_tags(user_pass)
        except Exception as e:
            logging.error(f"Failed to fetch tags for user {user['user']}: {e}")
            combined_playlist += f"# Failed to fetch tags for user {user['user']}: {e}\n"
            continue

        for tag in tags:
            tag_id = tag["tag_id"]
            tag_name = tag["name"]
            try:
                logging.info(f"Fetching channels for tag {tag_id} ({tag_name}) for user {user['user']}")
                m3u_text = fetch_channels_for_tag(tag_id, user_pass)
                m3u_with_injections = inject_group_titles(m3u_text, tag_name)
                m3u_with_auth = inject_auth(m3u_with_injections, user_pass)
                combined_playlist += m3u_with_auth
            except Exception as e:
                logging.error(f"Failed to fetch tag {tag_id} for user {user['user']}: {e}")
                combined_playlist += f"# Failed tag {tag_id} for user {user['user']}: {e}\n"

    if TVH_EPG_AUTH:
        logging.info("Checking if icon auth should be appended")
        combined_playlist = append_auth_to_tvg_logo(combined_playlist, TVH_EPG_AUTH)

    cached_playlist = combined_playlist
    last_refresh_time = current_time
    last_playlist_update = int(time.time())
    logging.info("Initial build: Playlist cache updated")
    channel_count = len(parse_m3u_channels(combined_playlist))
    logging.info(f"Initial build: Saved {channel_count} channels to the playlist.")
    socketio.emit('playlist_cache_refreshed')

    # Now enter the regular refresh loop
    while True:
        time.sleep(REFRESH_INTERVAL)
        logging.info("Auto-refresh: Building and caching playlist")
        current_time = time.time()
        combined_playlist = "#EXTM3U\n"
        for user in USERS:
            user_pass = user["pass"]
            try:
                logging.info(f"Fetching tags for user: {user['user']}")
                tags = fetch_tags(user_pass)
            except Exception as e:
                logging.error(f"Failed to fetch tags for user {user['user']}: {e}")
                combined_playlist += f"# Failed to fetch tags for user {user['user']}: {e}\n"
                continue

            for tag in tags:
                tag_id = tag["tag_id"]
                tag_name = tag["name"]
                try:
                    logging.info(f"Fetching channels for tag {tag_id} ({tag_name}) for user {user['user']}")
                    m3u_text = fetch_channels_for_tag(tag_id, user_pass)
                    m3u_with_injections = inject_group_titles(m3u_text, tag_name)
                    m3u_with_auth = inject_auth(m3u_with_injections, user_pass)
                    combined_playlist += m3u_with_auth
                except Exception as e:
                    logging.error(f"Failed to fetch tag {tag_id} for user {user['user']}: {e}")
                    combined_playlist += f"# Failed tag {tag_id} for user {user['user']}: {e}\n"

        if TVH_EPG_AUTH:
            logging.info("Checking if icon auth should be appended")
            combined_playlist = append_auth_to_tvg_logo(combined_playlist, TVH_EPG_AUTH)

        cached_playlist = combined_playlist
        last_refresh_time = current_time
        last_playlist_update = int(time.time())
        logging.info("Auto-refresh: Playlist cache updated")
        channel_count = len(parse_m3u_channels(combined_playlist))
        logging.info(f"Auto-refresh: Saved {channel_count} channels to the playlist.")
        socketio.emit('playlist_cache_refreshed')

def manual_refresh_playlist():
    global cached_playlist, last_refresh_time, last_playlist_update
    logging.info("Manual refresh: Building and caching playlist")
    current_time = time.time()
    combined_playlist = "#EXTM3U\n"
    for user in USERS:
        user_pass = user["pass"]
        try:
            logging.info(f"Fetching tags for user: {user['user']}")
            tags = fetch_tags(user_pass)
        except Exception as e:
            logging.error(f"Failed to fetch tags for user {user['user']}: {e}")
            combined_playlist += f"# Failed to fetch tags for user {user['user']}: {e}\n"
            continue

        for tag in tags:
            tag_id = tag["tag_id"]
            tag_name = tag["name"]
            try:
                logging.info(f"Fetching channels for tag {tag_id} ({tag_name}) for user {user['user']}")
                m3u_text = fetch_channels_for_tag(tag_id, user_pass)
                m3u_with_injections = inject_group_titles(m3u_text, tag_name)
                m3u_with_auth = inject_auth(m3u_with_injections, user_pass)
                combined_playlist += m3u_with_auth
            except Exception as e:
                logging.error(f"Failed to fetch tag {tag_id} for user {user['user']}: {e}")
                combined_playlist += f"# Failed tag {tag_id} for user {user['user']}: {e}\n"

    if TVH_EPG_AUTH:
        logging.info("Checking if icon auth should be appended")
        combined_playlist = append_auth_to_tvg_logo(combined_playlist, TVH_EPG_AUTH)

    cached_playlist = combined_playlist
    last_refresh_time = current_time
    last_playlist_update = int(time.time())
    logging.info("Manual refresh: Playlist cache updated")
    channel_count = len(parse_m3u_channels(combined_playlist))
    logging.info(f"Manual refresh: Saved {channel_count} channels to the playlist.")
    socketio.emit('playlist_cache_refreshed')

# Start the background thread after app and globals are set up
threading.Thread(target=build_and_cache_playlist, daemon=True).start()

if __name__ == "__main__":
    logging.info("Starting TVHeadend Playlist Server")
    logging.info(f"TVH_HOST: {TVH_HOST}")
    logging.info(f"TVH_PORT: {TVH_PORT}")
    logging.info(f"REFRESH_INTERVAL: {REFRESH_INTERVAL}")
    logging.info(f"TVH_USERS: {USER_CREDENTIALS}")
    logging.info(f"TVH_EPG_AUTH: {TVH_EPG_AUTH}")
    logging.info(f"TVH_APPEND_ICON_AUTH: {TVH_APPEND_ICON_AUTH}")
    logging.info("Web server is ready. Open http://localhost:9985/ in your browser.")
    socketio.run(app, host="0.0.0.0", port=9985)