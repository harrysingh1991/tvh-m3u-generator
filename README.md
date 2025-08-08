# tvh-m3u-genarator
Generate an M3U Playlist from TVHeadend Server, based on user/s and tag/s access. This container intends to improve the formatting of channel lists for IPTV Players e.g. Tivimate, Smarters. A web server will be available offering the urls for an M3U formatted channel list, and a proxied URL for the EPG

The script will do the following:

- Download all the tags set up on the TVH server.
- Download a channel list, for each user and tag combination, using the user credential provided in TVH_USERS (this can result in empty lists depending on users not having access to certain tags). Multiple users can be provided, seperated by a comma (see example Docker Compose below).
- Combine all the lists downloaded.
- Downloads will follow the tag order defined in TVHendend Server.
- Add a Group-Title TVG tag, based on the tag name.
- Add persistent key into the stream URLs (TVH_USERS persistent password).
- Optionally, add persistent password into icons url (TVH_EPG_AUTH, potentially required for local icons).
- Proxy EPG.
- EPG retreived using persistent password in its own variable (TVH_EPG_AUTH), to allow for EPG retrieval using an account with higher access than user accounts. This is to allow 1 TVH user with epg access to all channels.
- TVH Server may return EPG with Start and End times of shows being set to local time including DST, and also set an offet "+0100". In tivimate this causes the EPG to move an additional hour. EPG_STRIP_OFFSET can remove any offset TVH applies.
- System default streaming profile removed from URLs, which are automatically added when downloading channel lists. TVH will thrn choose streaming profile based on server setup/user access when a channel is played.
- Download a list and cache it, when the container is started. A refreshed list will be created once the refresh interval expires.

Web view added to do/see the following:

- Provide URL for Channel List and EPG XML files.
- Datetime stamp when cache list was last updated.
- Table showing the channels in the cached list (column for each attribute in the file).
- Light/Dark for easy viewing of different types of icons.
- Channel List manual refresh button, for use after TVH server changes and to avoid waiting or restarting of the container.

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
      TVH_USERS: "username:persistentpasswordhere,username2:password2"
      TVH_EPG_AUTH: "persistentpassword"
      #TVH_APPEND_ICON_AUTH: "1" #Optional. set to "1", "yes" or "true"
      #EPG_STRIP_OFFSET: "1" #Optional. Set to  to "1", "yes" or "true"
```

### Planned Improvements:

1. Empty lists insert an EXTM3U tag (users with restricted access to certain tags). Find a way to ignore empty tag lists (looks cleaner)
