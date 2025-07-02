# tvh-m3u-genarator
Generate M3U Playlist from TVHeadend Server based on tags

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
      SERVER_PORT: "9985" #Port for m3u and epg xml to be availble from
```
