# Running the record with Docker Compose

```
docker compose up --build     # first time, or after changing the code
docker compose up             # after that
docker compose down           # stop
```

Then open <http://127.0.0.1:7777>. Add `-d` to `up` to run it in the background.

**Linux only.** It uses host networking so the server binds `127.0.0.1` on this
computer itself, exactly as every other way of running it does — nothing binds
`0.0.0.0`, and nothing is reachable from another machine. Docker Desktop on macOS
and Windows does not share the host's loopback that way; use the desktop app there.

## Where things are kept

- **Your record** is the folder `./vault` beside this file, unless you set
  `HEALTH_VAULT_DIR` to another one (for example a folder inside Dropbox):

  ```
  HEALTH_VAULT_DIR=~/Dropbox/health docker compose up
  ```

  An empty or missing folder becomes a new record. A folder that already holds a
  record (it has `config.toml`) is opened, and nothing in it is rewritten. A
  folder with anything else in it is refused, and the container stops saying so.
  `./vault` is in `.gitignore` and `.dockerignore`: a record never goes into git
  or into an image.

- **This container's identity as a device, the reader choice, and the downloaded
  reader** (about 4 GB) live in the `health-machine` volume, so `down` and `up`
  is the same device and does not download again. `docker compose down -v`
  deletes that volume — the next start is a new device and downloads again. The
  record folder is never touched by `-v`.

## Things that differ from the desktop app

- **Your user id.** Files in the record belong to uid/gid 1000. If `id -u` says
  otherwise, build with `HEALTH_UID=$(id -u) HEALTH_GID=$(id -g) docker compose up --build`.
- **No keychain.** For an inference box on another computer, export the key as
  `HEALTH_VLM_TOKEN` before `up` (and name that variable in `api_key_env` under
  `[models.vlm.auth]`). Compose passes it through only when it is set. It is
  never written into the record folder.
- **Commands the interface mentions** run inside the container:
  `docker compose exec health-record health-agent check`.
- **Port 7777 must be free**, so don't run the desktop app against the same port
  at the same time.
