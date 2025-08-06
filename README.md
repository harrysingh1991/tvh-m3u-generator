# tvh-m3u-genarator
Generate an M3U Playlist from TVHeadend Server, for user/s based on tag/s access. This container intends to improve the formatting of channel lists for IPTV Players e.g. Tivimate, Smarters. A web server will be available offering the urls for an M3U formatted channel list and a proxied url for the epg.xml

The script will do the following:

- Download all the tags set up on the TVH server, for the users provided in TVH_USERS. This allows for multiple users to be requested.
- Download a channel list, for each tag and user, using the user credential provided in TVH_USERS (this can result in empty lists depending on users not having access to certain tags), and then download each list. Multiple users can be provided, sperated by a comma (see example Docker Compose below)
- Combine all the lists downloaded
- Downloads will follow the tag order defined in TVHendend Server
- Add a Group-Title TVG tag, based on the tag name
- Add persistent key into the stream URLs (TVH_USERS persistent password)
- Add persistent key into icons url (TVH_EPG_AUTH, required for local icons)
- EPG proxied
- EPG retreived using persistent password in its own variable (TVH_EPG_AUTH), to allow for EPG retrieval using an account with higher access than user accounts. This is to allow 1 TVH user with epg access to all channels.
- System default streaming profile removed from URLs, which are automatically added when downloading channel lists. TVH will thrn choose streaming profile based on server setup/user access when a channel is played.
- The list is only refreshed after the cache period, when a list is requested. i.e. a list could be 4 hours out of date, and then when the list URL is requested it will recognise the list is out of date, and generate an updated version.

Web view added to do/see the following:

- Provide URL for Channel List and EPG XML files
- Datetime stamp when cache list was last updated
- Table showing the channels in the cached list (column for each attribute in the file)
- Light/Dark for easy viewing of different types of icons
- Channel List manual refresh button, for use after TVH server changes and to avoid waiting.

IMPORTANT NOTE: A list is generated after the first time the playlist URL is called, and not when the container is started

Docker Compose Example:

```
services:
  tvh-m3u-generator:
    image: harrysingh1991/tvh-m3u-generator:latest
    container_name: tvh-m3u-generator
    ports:
      - "9985:9985"
    environment:
      TVH_HOST: "Enter TVH Server or Proxy" #e.g. 192.168.0.2 
      TVH_PORT: "9981" # TVH Port
      REFRESH_INTERVAL: "600" #cache timeout in seconds
      SERVER_PORT: "9985" #Port for m3u and epg xml to be available from
      TVH_USERS: "username:persistentpasswordhere,username2:password2"
      TVH_EPG_AUTH: "persistentpassword"
```

### Planned Improvements:

In no particular order:

1. Empty lists insert an EXTM3U tag (users with restricted access to certain tags). Find a way to ignore empty tag lists (looks cleaner)
2. Add a variable to define if persistent password needs adding to channel icon url. If icons are retreived from a non-local source, then an auth code probably isn't needed.
