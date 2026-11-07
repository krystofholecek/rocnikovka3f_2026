# Data a zdroje

## Pravidlo data

Do tréninku smí jen text, u kterého **zdroj uvádí datum nejpozději 31. 12. 2005**. Datum bereme jako pravdivé
(neodhadujeme ho z obsahu). Dokument bez data nebo s pozdějším datem se uloží do tabulky `rejected` s důvodem.
Falešná „nulová“ data (1. 1. 1970, 31. 12. 1979 apod. – výchozí hodnoty chybných hlaviček) považujeme za chybějící;
518 takových dokumentů jsme dodatečně přesunuli mezi zamítnuté.

## Přehled zdrojů

| Zdroj | Parser (`llm2005/sources/`) | Jak se datuje | Stažení / import |
|---|---|---|---|
| Wikipedie – články | `wikipedia.py` | poslední revize do 31. 12. 2005 | archiv `enwiki-20060816-pages-meta-history` (archive.org); `python -m llm2005.ingest wikipedia <soubor>` |
| Wikipedie – diskuze, Wikisource, Wikinews, Wikibooks, Wikiquote | `mediawiki.py` | poslední revize do 31. 12. 2005 | dumpy Wikimedia z roku 2006; `python -m llm2005.download wikimedia` |
| Knihy PG-19 | `pg19.py` | rok vydání | `python -m llm2005.download pg19` |
| E-maily Enron | `enron.py` | hlavička `Date:` | `python -m llm2005.download enron` |
| Blogy | `blogs.py` | datum příspěvku | `python -m llm2005.download blogs` |
| Usenet UTZOO | `usenet.py` | hlavička `Date:` | `python -m llm2005.download utzoo` |
| Soudní rozhodnutí USA | `courtlistener.py` | datum rozhodnutí | `python -m llm2005.download courtlistener` |
| Abstrakty arXiv | `arxiv.py` | datum poslední verze (pozdější verze = zahodit) | `python -m llm2005.download arxiv` |
| Vlastní data | `jsonl.py` | pole `date` | `python -m llm2005.ingest jsonl soubor.jsonl` (pole `source`, `source_id`, `date`, `text`) |

Import velkých zdrojů najednou: `python -m llm2005.modern_data`. Surová stažená data jsou mimo repo
(na tréninkovém PC na disku D:), v repu je jen kód.

## Licence a citace

Data **nezveřejňujeme** (ani v repu, ani na webu) – používáme je jen k tréninku modelu v rámci školní práce.
Zveřejňujeme kód a případně natrénovaný model. Před zveřejněním vah je dobré podmínky zdrojů znovu ověřit;
u položek označených *ověřit* jsme podmínky nenašli jednoznačně.

| Zdroj | Licence / podmínky | Citace |
|---|---|---|
| Wikipedie (en) | CC BY-SA 3.0 a GFDL (texty z let 2001–2005 byly původně pod GFDL, v roce 2009 je Wikimedia převedla i pod CC BY-SA) | Wikipedia contributors. *English Wikipedia*, dump `enwiki-20060816`. Wikimedia Foundation. |
| Wikisource, Wikibooks, Wikiquote | CC BY-SA / GFDL (volná díla na Wikisource jsou volné dílo) | Wikimedia projects, dumpy z prosince 2006. |
| Wikinews | CC BY 2.5 | Wikinews contributors, dump z prosince 2006. |
| PG-19 | data Apache 2.0, knihy jsou volné dílo (Project Gutenberg, vydané před rokem 1919) | Rae, J. W., Potapenko, A., Jayakumar, S. M., Lillicrap, T. P. *Compressive Transformers for Long-Range Sequence Modelling.* ICLR 2020. |
| Enron Email Dataset | zveřejněno americkou FERC při vyšetřování; k výzkumu volně (*ověřit* podmínky CMU) | Klimt, B., Yang, Y. *The Enron Corpus: A New Dataset for Email Classification Research.* ECML 2004. Cohen, W. W. *Enron Email Dataset*, Carnegie Mellon University. |
| Blog Authorship Corpus | jen pro nekomerční výzkum | Schler, J., Koppel, M., Argamon, S., Pennebaker, J. *Effects of Age and Gender on Blogging.* AAAI Spring Symposium 2006. |
| Usenet UTZOO | příspěvky jsou dílem svých autorů; archiv zpřístupněn pro historický výzkum (*ověřit*) | Spencer, H. *UTZOO Usenet archive 1981–1991* (pásky University of Toronto), archive.org. |
| CourtListener | soudní rozhodnutí USA jsou volné dílo; hromadná data Free Law Project (*ověřit* podmínky) | Free Law Project. *CourtListener bulk data.* courtlistener.com. |
| arXiv – metadata a abstrakty | metadata arXiv včetně abstraktů jsou pod CC0 1.0 | arXiv.org, rozhraní OAI-PMH, záznamy 1991–2005. |

## Čísla

Aktuální počty dokumentů a znaků jsou v [README](../README.md#data) (`python -m llm2005.ingest stats`).
Statistiky čištění, tokenizéru a přípravy dat se zapisují do `data/progress/*.json` (nejsou v gitu).

## Nový zdroj

1. Nový soubor `llm2005/sources/<zdroj>.py` s funkcí, která vrací dokumenty (zdroj, id, datum, text).
2. Záznam do `SOURCES` v `llm2005/sources/__init__.py` (klíč jen malými písmeny).
3. Test s malým vzorkem dat v `llm2005/tests/fixtures/`.
4. Řádek do tabulek výše, včetně licence a citace.
