# Nasazení

Web a chat se nasazují zvlášť:

| Část | Kde | Jak |
|---|---|---|
| Web (`web/`) | Cloudflare Workers, chronosllm.com | automaticky po každém mergi do `main` |
| API chatu (`chat/`) | VPS nebo domácí PC, Docker | ručně (`docker compose`), za Cloudflare Tunnel na api.chronosllm.com |

## Web: Cloudflare Workers Builds

Nastavení v Cloudflare (Workers & Pages → Worker **chronosllm** → Settings → Builds), jednou:

| Nastavení | Hodnota |
|---|---|
| Git repository | `krystofholecek/rocnikovka3f_2026` |
| Production branch | `main` |
| Root directory | `/` |
| Build command | `python scripts/build_site.py` |
| Deploy command | `npx wrangler deploy` |
| Build variable (volitelné) | `CHAT_API_URL=https://api.chronosllm.com` (výchozí hodnota) |

- `scripts/build_site.py` složí `dist/`: úvodní stránku, `/postup/`, `/chat/` (s adresou API) a `/tokenizer/`,
  a zapíše `dist/version.json` (commit, větev, čas buildu, adresa API).
- `wrangler.jsonc`: jen statické soubory z `./dist`, vlastní domény chronosllm.com a www, `workers_dev: false`.
- Ověření nasazené verze: https://chronosllm.com/version.json – `commit` musí odpovídat poslednímu commitu v `main`.
- Lokálně: `python scripts/build_site.py && python -m http.server -d dist 8080`.

## API chatu: Docker + Cloudflare Tunnel

Server nemá otevřený žádný port; `cloudflared` se připojí k Cloudflare odchozím spojením a ten posílá
požadavky z `api.chronosllm.com` do kontejneru. Podrobný popis API a proměnných: [chat/README.md](../chat/README.md).

### Jednou: tunel v Cloudflare

1. Zero Trust → Networks → Tunnels → **Create a tunnel** (cloudflared), název např. `chronosllm-api`.
2. Token tunelu zkopíruj do `.env` na serveru jako `CLOUDFLARE_TUNNEL_TOKEN` (do gitu nikdy).
3. Public hostname: `api.chronosllm.com` → Service `HTTP` `chat:8000`.
4. **Cloudflare Access nesmí chránit `api.chronosllm.com`** (prohlížeč by místo odpovědi dostal přihlašovací stránku).
   Pokud má být chat zatím jen pro tým, nech Access na `chronosllm.com/chat/`.

### Server

```bash
git clone https://github.com/krystofholecek/rocnikovka3f_2026.git && cd rocnikovka3f_2026
cp .env.example .env      # vyplnit DJANGO_SECRET_KEY, CLOUDFLARE_TUNNEL_TOKEN,
                          # DJANGO_ALLOWED_HOSTS=api.chronosllm.com,localhost,127.0.0.1, DJANGO_DEBUG=False
mkdir -p checkpoints/chat # sem model.pt a tokenizer.json (viz níže)
GIT_COMMIT=$(git rev-parse HEAD) docker compose --profile tunnel up -d --build
curl http://127.0.0.1:8000/api/health
```

- **VPS**: stačí 2 jádra a 2–4 GB RAM, bez GPU.
- **Domácí PC (Windows)**: stejné s Docker Desktop. Když je PC vypnuté, web ukáže „Model se zrovna trénuje“.
- Aktualizace kódu: `git pull && docker compose --profile tunnel up -d --build`.

## Až bude model hotový

1. **Na tréninkovém PC** vyexportuj model (z kořene repa):
   ```bash
   python -m llm2005.export --config configs/base.json      # nebo small.json; --half pro menší soubor
   ```
   Vznikne `checkpoints/chat/model.pt` a `checkpoints/chat/tokenizer.json` a vypíše se SHA-256 otisk souboru.
2. Rychlá zkouška na stejném PC:
   ```bash
   python -m llm2005.sample --config configs/base.json --prompt "The Internet is"
   DJANGO_DEBUG=true python manage.py runserver     # a pak curl http://127.0.0.1:8000/api/health -> "ready": true
   ```
3. **Zkopíruj oba soubory na server** do `checkpoints/chat/`
   (např. `scp checkpoints/chat/* server:~/rocnikovka3f_2026/checkpoints/chat/`) a ověř otisk (`sha256sum checkpoints/chat/model.pt`).
4. Server model načte sám do 30 s; jistější je restart: `docker compose restart chat`.
5. Ověř: `https://api.chronosllm.com/api/health` → `"ready": true` a správný `step`; pak `https://chronosllm.com/chat/`.
6. Na úvodní stránce (`web/landing/index.html`) změň popisek tlačítka „Chat s modelem (brzy)“ na „Chat s modelem“
   a merguj do `main` – web se nasadí sám.

Výměna za novější model = kroky 1, 3 a 4 znovu. Starý `model.pt` si nech jako zálohu.
