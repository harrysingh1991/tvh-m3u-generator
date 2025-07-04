# tvh-m3u-genarator
Generate an M3U Playlist from TVHeadend Server, for a user based on tags access. This is intended for tvheadend channel lists being formatted better for IPTV players e.g. tivimate

The script will do the following:

- Download all the tags set up on the TVH server
- Download a channel list for each tag, using the user credential provided in TVH_PERSISTENT_PASS (this can result in empty lists depending on user access)
- Combine all the lists (empty lists are ignored)
- Add a Group-Title TVG tag, based on the tag name
- Add persistent key into the stream URLs
- EPG proxied

A list is generated after the first time the playlist URL is called.

The playlist and epg are accessible from a web server via the machine's IP. The URL's also automatically change if the web server is accessed by a local dns name or proxy.

Docker Compose Example:

```
services:
  tvh-m3u-generator:
    image: harminderdhak/tvh-m3u-generator:latest
    container_name: tvh-m3u-generator
    ports:
      - "9985:9985"
    environment:
      TVH_HOST: "Enter TVH Server or Proxy"
      TVH_PORT: "9981" # TVH Port
      TVH_PERSISTENT_PASS: "PASSWORD" # Use Persistent Password
      REFRESH_INTERVAL: "600" # Refresh interval in seconds
      SERVER_PORT: "9985" #Port for m3u and epg xml to be available from
```

### Planned Improvements:

In no particular order:

1. Create channel list when container is started and cache it
2. Return a cached list upon every request
3. List to update depending on refresh interval. The script currently has a refresh interval and caches the list, but does nothing with that
