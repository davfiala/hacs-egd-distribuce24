# EG.D Distribuce24 pro Home Assistant

Vlastní integrace pro denní stahování elektřiny z EG.D OpenAPI. Verze **0.1.1 – beta**.
Repozitář: https://github.com/davfiala/hacs-egd-distribuce24

Instalace přes HACS jako vlastní repozitář typu **Integrace**.
Integrace není zařazena do výchozího katalogu HACS.

## Co umí

- Nastavení přes uživatelské rozhraní; žádný YAML ani přístupové údaje ve zdrojovém kódu.
- Výběr jednoho nebo více odběrných míst podle EAN. Stahují se pouze vybraná místa.
- Měření C1: odběr `DCQC`, volitelně přetoky `DSQC`.
- Měření A/B: energie odběru `ICQ2`, volitelně přetoky `ISQ2` (nikoli výkon v kW).
- Token uchovávaný jen v paměti, obnova každý den a jeden opakovaný pokus při odmítnutí tokenu.
- Počáteční historie až 30 dní podle oprávnění účtu, následně denní kontrola s překryvem 14 dní pro opravy a pozdní odečty.
- Po delším vypnutí dočtení od posledního staženého období, dotazy rozdělené po 28 dnech.
- Externí hodinové statistiky pro panel Energie. Opakování a opravy historie nepřičítají spotřebu znovu.
- Senzory poslední čtvrthodiny, času odečtu a poslední synchronizace pro každý profil.
- České a anglické nastavení.

## Ruční instalace nyní

1. Použijte Home Assistant **2026.1 nebo novější** se zapnutým `recorder` (standardní součást).
2. Zkopírujte adresář `custom_components/egd_distribuce24` z balíčku do stejného umístění
   v konfiguračním adresáři Home Assistantu. Výsledkem má být
   `/config/custom_components/egd_distribuce24/manifest.json`.
3. Restartujte Home Assistant.
4. Otevřete **Nastavení → Zařízení a služby → Přidat integraci → EG.D Distribuce24**.
5. Vyplňte `client_id`, `client_secret` a podle potřeby zapněte přetoky.
6. Vyberte požadovaná odběrná místa a potvrďte.

Pokud bude potřeba změnit vybraná místa nebo zapnout přetoky později, v této beta verzi
integraci odeberte a přidejte znovu. Existující externí statistiky se tím automaticky nemažou;
před změnou rozsahu historie je vhodná záloha databáze. Změna přístupových údajů při jejich
odmítnutí používá standardní opětovné přihlášení a zachová původní výběr míst.

## Panel Energie

V nastavení panelu Energie vyberte v odběru ze sítě statistiku s názvem
`EG.D <EAN> DCQC` (nebo `ICQ2` pro A/B). Pro návrat do sítě vyberte `DSQC`/`ISQ2`.
Identifikátory mají tvar `egd_distribuce24:<ean>_dcqc`.
Statistika se objeví až po načtení alespoň jedné úplné hodiny a zpracování fronty Recorderu.

Senzor „Poslední čtvrthodina“ je pouze přehled posledního odečtu. Není kumulativní měřidlo
a nepoužívá se jako vstup do panelu Energie. Hodnoty se importují k historickým hodinám,
nikoli k okamžiku jejich stažení.

## Plán a kvalita dat

První stažení proběhne při nastavení. Poté integrace jednou za hodinu lokálně ověří,
zda nastal čas denního stažení: první kontrola po **12:17 v pásmu Europe/Prague**.
Po úspěšném odpoledním stažení se další síťové volání ten den neprovádí.
Pokud první stažení proběhlo ráno, proběhne ještě odpolední aktualizace.
Při chybě se běžná aktualizace opakuje nejdříve při příštím hodinovém cyklu;
opakování neúspěšného prvního nastavení řídí Home Assistant.

Stahování začíná nejnovějšími daty. Pokud EG.D odmítne starší období kvůli
rozsahu oprávnění, dotaz se postupně zkrátí až na jeden den. Po dosažení první
nepřístupné starší části se vrátí již načtená historie. Pokud není přístupný ani
nejnovější den, chyba zůstává viditelná; jiné chyby HTTP 400 se nezamlčují.

Konec dotazu odpovídá včerejšímu dni v Praze. Interně se ukládají časy UTC, takže nedochází
ke sloučení opakovaných hodin při změně letního času.

- Přijímány jsou stavy `W`, `B`, `E`, `M`, `N` (platné a platné náhradní hodnoty).
- `F`, `G`, `V`, neznámé stavy a chybějící hodnoty se do nových statistik nezahrnují.
- Publikují se pouze hodiny se čtyřmi platnými čtvrthodinami. Chybějící hodina není nula.
- Již uložená úplná hodina zůstane zachována, pokud další odpověď obsahuje neúplná nebo
  dočasná data; nahradí se, až dorazí nová úplná platná hodina.
- Opravy starší než 14 dní se při běžném denním stahování automaticky nevyhledávají.
- Při mezerách nemusejí součty pokrývat celé zvolené období; panel Energie může podle
  svého způsobu agregace rozložit rozdíl mezi dostupnými kumulativními body.
- Časová značka se v této verzi interpretuje jako začátek čtvrthodiny. Návod EG.D
  tuto konvenci výslovně nedefinuje; před ostrým použitím ji porovnejte s exportem Distribuce24.

Hodinový archiv se ukládá do `.storage/egd_distribuce24.<entry_id>`. Uchovává celou
načtenou historii a při startu ji znovu odešle do Recorderu. Úplné přepočítání součtů
umožňuje opravy historie a obnovu po přerušení mezi uložením a importem. Objem archivu
i práce při importu postupně rostou; verze 0.1 cílí na domácnosti s malým počtem míst.
Nevymazávejte archiv samostatně, pokud chcete zachovat stejný počátek kumulativních součtů.

## HACS a zveřejnění

1. V HACS otevřete **Vlastní repozitáře**.
2. Vložte `https://github.com/davfiala/hacs-egd-distribuce24` a vyberte typ **Integrace**.
3. Nainstalujte integraci, restartujte HA a pokračujte nastavením výše.

Při vydávání další verze sjednoťte číslo v manifestu s tagem GitHub Release.

Zařazení do výchozího katalogu HACS je samostatný proces; tato beta verze je určena
nejprve pro vlastní repozitář. Nejde o oficiální integraci EG.D.

## Vývoj a ověření

```sh
python -m pip install -r requirements-test.txt
python -m pytest tests
ruff check .
ruff format --check .
```

Testy pokrývají klienta, agregaci, přechody letního času, opakované importy, opravy a
koordinátor s náhradami rozhraní Home Assistantu. Nejde o plnohodnotný test běžícího HA.
Neověřená sada `tests_ha/` je ponechána pro případný budoucí vývoj; v CI se nespouští.
Instalaci a funkčnost v Home Assistantu ověřuje vlastník ručně.
Podrobnosti provedených a zbývajících kontrol jsou v `VALIDATION.md`.

Přístupové údaje jsou pouze v konfiguraci HA. Token se neukládá. Chybová hlášení
neobsahují těla odpovědí ani přístupové údaje. Stejně jako ostatní konfiguraci HA
je třeba chránit i zálohy obsahující konfiguraci účtu.

## Zdroje

- [Návod EG.D OpenAPI, květen 2026](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf)
- [Home Assistant – konfigurace integrací](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant – metadata statistik](https://developers.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/)
- [HACS – struktura integrace](https://www.hacs.dev/docs/publish/integration/)
