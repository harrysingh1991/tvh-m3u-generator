import eventlet
eventlet.monkey_patch()

import os
import time
import logging
import requests
from flask import Flask, Response, redirect, url_for
import re
from dotenv import load_dotenv
import urllib.parse
import datetime
from flask_socketio import SocketIO, emit

# Load environment variables from a .env file (if present)
load_dotenv()

# Initialize Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

# Read required environment variables with defaults
TVH_HOST = os.getenv("TVH_HOST", "127.0.0.1")  # TVHeadend server IP
TVH_PORT = int(os.getenv("TVH_PORT", 9981))   # TVHeadend server port
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", 600))  # Playlist cache duration in seconds
SERVER_PORT = int(os.getenv("SERVER_PORT", 9987))  # Flask server port
USER_CREDENTIALS = os.getenv("TVH_USERS", "")  # Format: user1:pass1,user2:pass2,...
TVH_EPG_AUTH = os.getenv("TVH_EPG_AUTH")  # persistent password for epg retrieval

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

# Append auth token to tvg-logo URLs in the M3U playlist
def append_auth_to_tvg_logo(m3u_text, auth_token):
    def repl(match):
        url = match.group(1)
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
        return f'tvg-logo="{new_url}"'

    pattern = r'tvg-logo="([^"]+)"'
    return re.sub(pattern, repl, m3u_text)

# Add a manual refresh endpoint
@app.route("/refresh")
def refresh():
    global cached_playlist, last_refresh_time
    last_refresh_time = 0
    playlist()
    socketio.emit('playlist_updated')
    logging.info("Playlist cache manually refreshed")
    return redirect(url_for('index'))

# Parse channels from M3U text
def parse_m3u_channels(m3u_text):
    channels = []
    lines = m3u_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            # Extract attributes
            group_title = re.search(r'group-title="([^"]+)"', line)
            tvg_id = re.search(r'tvg-id="([^"]+)"', line)
            tvg_logo = re.search(r'tvg-logo="([^"]+)"', line)
            channel_number = re.search(r'tvg-chno="([^"]+)"', line)
            # Channel name is after last comma
            channel_name = line.split(",", 1)[-1].strip()
            # Next line should be the stream URL
            stream_url = lines[i+1] if i+1 < len(lines) else ""
            # Extract channelid from stream_url
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
            i += 2
        else:
            i += 1
    return channels

# Track server start time for restart detection
SERVER_START_TIME = int(time.time())

@app.route("/server_status")
def server_status():
    return {"start_time": SERVER_START_TIME}

# Main index page showing download links and info
@app.route("/")
def index():
    if last_refresh_time:
        last_update_str = datetime.datetime.fromtimestamp(last_refresh_time).strftime("%Y-%m-%d %H:%M:%S")
    else:
        last_update_str = "Never"

    channel_rows = ""
    if cached_playlist:
        channels = parse_m3u_channels(cached_playlist)
        for ch in channels:
            logo_html = f'<img src="{ch["tvg_logo"]}" alt="logo" style="height:32px;">' if ch["tvg_logo"] else ""
            play_html = f'''
            <button onclick="copyToClipboard('{ch["stream_url"]}')">Copy Link</button>
            '''
            channel_rows += f"""
            <tr>
                <td>{ch["group_title"]}</td>
                <td>{ch["channel_name"]}</td>
                <td>{ch["channel_number"]}</td>
                <td>{ch["tvg_id"]}</td>
                <td>{ch.get("channelid", "")}</td>
                <td>{logo_html}</td>
                <td>{play_html}</td>
            </tr>
            """

    html = f"""
    <html>
    <head>
        <title>TVHeadend Playlist Server</title>
        <style>
            body {{
                background-color: #181818;
                color: #e0e0e0;
                font-family: Arial, sans-serif;
                transition: background 0.3s, color 0.3s;
            }}
            table {{
                background-color: #222;
                color: #e0e0e0;
                border-collapse: collapse;
                width: 100%;
            }}
            th, td {{
                border: 1px solid #444;
                padding: 8px;
                text-align: left;
            }}
            th {{
                background-color: #333;
            }}
            tr:nth-child(even) {{
                background-color: #202020;
            }}
            a, a:visited {{
                color: #80bfff;
            }}
            button {{
                background-color: #333;
                color: #e0e0e0;
                border: 1px solid #444;
                padding: 8px 16px;
                border-radius: 4px;
                cursor: pointer;
            }}
            button:hover {{
                background-color: #444;
            }}
            img {{
                background: #222;
                border-radius: 4px;
            }}
            /* Light mode styles */
            body.light-mode {{
                background-color: #f5f5f5;
                color: #222;
            }}
            table.light-mode {{
                background-color: #fff;
                color: #222;
            }}
            th.light-mode, td.light-mode {{
                border: 1px solid #ccc;
            }}
            th.light-mode {{
                background-color: #eee;
            }}
            tr.light-mode {{
                background-color: #fff !important;
                color: #222 !important;
            }}
            tr.light-mode:nth-child(even) {{
                background-color: #f0f0f0 !important;
            }}
            a.light-mode, a.light-mode:visited {{
                color: #0066cc;
            }}
            button.light-mode {{
                background-color: #eee;
                color: #222;
                border: 1px solid #ccc;
            }}
            button.light-mode:hover {{
                background-color: #ddd;
            }}
            img.light-mode {{
                background: #fff;
            }}
        </style>
        <script src="//cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
        <script>
            var socket = io({{transports: ['websocket']}});
            socket.on('playlist_updated', function() {{
                location.reload();
            }});

            // Add polling for server restart detection
            let lastServerStart = null;
            function checkServerRestart() {{
                fetch('/server_status')
                    .then(response => response.json())
                    .then(data => {{
                        if (lastServerStart === null) {{
                            lastServerStart = data.start_time;
                        }} else if (data.start_time !== lastServerStart) {{
                            location.reload();
                        }}
                    }})
                    .catch(() => {{}});
            }}
            setInterval(checkServerRestart, 5000);

            function toggleMode() {{
                document.body.classList.toggle('light-mode');
                var tables = document.getElementsByTagName('table');
                for (var i = 0; i < tables.length; i++) {{
                    tables[i].classList.toggle('light-mode');
                }}
                var ths = document.getElementsByTagName('th');
                for (var i = 0; i < ths.length; i++) {{
                    ths[i].classList.toggle('light-mode');
                }}
                var tds = document.getElementsByTagName('td');
                for (var i = 0; i < tds.length; i++) {{
                    tds[i].classList.toggle('light-mode');
                }}
                var links = document.getElementsByTagName('a');
                for (var i = 0; i < links.length; i++) {{
                    links[i].classList.toggle('light-mode');
                }}
                var buttons = document.getElementsByTagName('button');
                for (var i = 0; i < buttons.length; i++) {{
                    buttons[i].classList.toggle('light-mode');
                }}
                var imgs = document.getElementsByTagName('img');
                for (var i = 0; i < imgs.length; i++) {{
                    imgs[i].classList.toggle('light-mode');
                }}
                var trs = document.getElementsByTagName('tr');
                for (var i = 0; i < trs.length; i++) {{
                    trs[i].classList.toggle('light-mode');
                }}
            }}

            function copyToClipboard(url) {{
                // Create a temporary textarea element
                var tempInput = document.createElement("textarea");
                tempInput.value = url;
                document.body.appendChild(tempInput);
                tempInput.select();
                try {{
                    document.execCommand("copy");
                    alert("Stream URL copied! Paste it in VLC or your preferred player.");
                }} catch (err) {{
                    alert("Failed to copy. Please copy manually.");
                }}
                document.body.removeChild(tempInput);
            }}
        </script>
    </head>
    <body>
        <h1>TVHeadend Playlist Server</h1>
        <button onclick="toggleMode()">Toggle Dark/Light Mode</button>
        <ul>
            <li><a href="/playlist.m3u">Download M3U Playlist</a></li>
            <li><a href="/epg.xml">Download EPG XML</a></li>
        </ul>
        <form action="/refresh" method="get">
            <button type="submit">Refresh Channel List Now</button>
        </form>
        <p>Playlist refresh interval: {REFRESH_INTERVAL} seconds</p>
        <p>Last playlist update: {last_update_str}</p>
        <h2>Channels</h2>
        <table>
            <tr>
                <th>Group Title</th>
                <th>Channel Name</th>
                <th>Channel Number</th>
                <th>TVG-ID</th>
                <th>Channel ID</th>
                <th>TVG-Logo</th>
                <th>Play</th>
            </tr>
            {channel_rows}
        </table>
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

    # Append auth to all tvg-logo URLs for all channels in the combined playlist
    if TVH_EPG_AUTH:
        combined_playlist = append_auth_to_tvg_logo(combined_playlist, TVH_EPG_AUTH)

    # Update cache
    cached_playlist = combined_playlist
    last_refresh_time = current_time
    logging.info("Generated updated playlist")

    return Response(combined_playlist, mimetype="application/x-mpegurl")

# EPG endpoint — proxies XMLTV from TVHeadend using the persistent EPG auth user
@app.route("/epg.xml")
def epg():
    try:
        if not TVH_EPG_AUTH:
            raise Exception("TVH_EPG_AUTH is not set.")

        # Append the persistent password to get the full EPG
        epg_url = f"http://{TVH_HOST}:{TVH_PORT}/xmltv/channels?auth={TVH_EPG_AUTH}"
        logging.info(f"Proxying full EPG from: {epg_url}")
        resp = requests.get(epg_url)
        resp.raise_for_status()
        return Response(resp.content, mimetype="application/xml")
    except Exception as e:
        logging.error(f"Failed to fetch EPG XML: {e}")
        return f"Failed to fetch EPG XML: {e}", 500

# Entry point for the Flask server
if __name__ == "__main__":
    logging.info(f"Starting TVHeadend Playlist Server on port {SERVER_PORT}")
    socketio.run(app, host="0.0.0.0", port=SERVER_PORT)