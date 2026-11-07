# Bezpečnost a soukromí

## Tajné údaje

- Tajné hodnoty (`DJANGO_SECRET_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`) jsou jen v souboru `.env` na serveru, který
  **není v gitu** (`.gitignore`). Vzor bez hodnot je v `.env.example`.
- Bez `DJANGO_SECRET_KEY` server nenastartuje; `DEBUG` je ve výchozím stavu vypnutý.
- Když tajný údaj unikne (např. omylem v commitu), hned ho vyměň: nový klíč / nový token tunelu v Cloudflare.
  Smazání commitu nestačí.

## API chatu

| Ochrana | Jak |
|---|---|
| Jen text, žádný kód | model jen generuje text; checkpoint se načítá s `torch.load(weights_only=True)`, takže soubor nemůže spustit kód |
| Délka vstupu a výstupu | max. 1000 znaků otázky, 4000 znaků historie, 256 nových tokenů, tělo požadavku max. 64 kB |
| Čas | generování se ukončí po 45 s |
| Zátěž | limit dotazů z jedné IP (10/min, 200/den), nejvýš 2 odpovědi najednou, jinak 429 / 503 |
| Kdo smí volat | CORS jen pro `https://chronosllm.com` a `https://www.chronosllm.com`; POST z jiné stránky = 403 |
| Hlavičky | `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store` |
| Síť | kontejner poslouchá jen na 127.0.0.1, ven vede Cloudflare Tunnel; žádný otevřený port |
| Kontejner | neprivilegovaný uživatel, souborový systém jen pro čtení, model připojený jen pro čtení, bez Linux capabilities, limit paměti a CPU |

Všechny limity jdou změnit proměnnými prostředí (viz [chat/README.md](../chat/README.md)).
IP adresu návštěvníka bereme z hlavičky `CF-Connecting-IP` jen tehdy, když je server dostupný výhradně přes Cloudflare
(jinak by šla hlavička podvrhnout) – v `docker-compose.yml` je to tak nastavené.

## Soukromí

- Konverzace se **nikam neukládají** – ani na serveru (žádná databáze, přístupový log je vypnutý),
  ani v prohlížeči (stránka je drží jen v paměti, po zavření zmizí).
- Server si pamatuje jen anonymní počty (kolik dotazů, tokenů, chyb) – vidět v `/api/health`.
- IP adresa se drží jen v paměti po dobu limitního okna a jen jako otisk (SHA-256 s náhodnou solí, která se
  při každém startu mění).
- Web nepoužívá cookies, analytiku ani externí skripty; do `localStorage` ukládá jen volbu světlého/tmavého režimu.

## Data

Trénovací data, databáze a checkpointy nejsou v gitu ani na webu (`.gitignore`). Licence zdrojů:
[data-a-zdroje.md](data-a-zdroje.md#licence-a-citace).

## Nahlášení problému

Bezpečnostní problém nepiš do veřejného issue – napiš přímo správci repa (Kryštof Holeček).
