# Docker

Docker Compose is the recommended installation method.

Clone the repository and run:

```shell
docker compose up --build -d
```

The included Compose file mounts `./config` and `./data`. A larger example:

```yaml
services:
  mlm:
    build: .
    ports:
      - "3157:3157"
    volumes:
      - ./config:/config # folder for the config file, place it in config/config.toml
      - ./data:/data # folder where mlm will keep a database
      - /mnt/Data:/mnt/Data # folder where your downloaded files and library can be accessed from
    environment:
      TZ: Europe/London # https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
```

