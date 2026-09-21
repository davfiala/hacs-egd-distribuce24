# Ověření verze 0.1.0

Provedeno 21. 9. 2026.

## Provedené kontroly

- 39 automatických testů klienta, historických součtů a koordinátoru prošlo.
- Kontrola a formátování Pythonu nástrojem Ruff.
- Test nového klienta proti produkčnímu API s uživatelem poskytnutým přístupem:
  - přihlášení a seznam odběrných míst fungují;
  - `DCQC`: 192 čtvrthodinových záznamů, 48 úplných hodin za dva dny;
  - `DSQC`: úspěšná odpověď bez záznamů, proto nebyly vytvořeny statistiky přetoků.
- Produkční odpověď `/spotreby` je objekt, návod uvádí seznam. Klient podporuje obojí.
- Ověřeno omezení konce dotazu na včerejší den podle českého času.
- Výběr míst je uplatněn v koordinátoru; test ověřuje, že se ostatní EAN nestahují.
- Přístupové údaje, token a skutečný EAN nejsou součástí zdrojového balíčku.

## Co zatím není ověřeno

- Instalace, formuláře a panel Energie v běžícím Home Assistantu.
- Sada `tests_ha` se skutečnými rozhraními HA je připravena v CI, zde nebyla spuštěna.
  Místní Windows runtime má Python 3.12; dostupné Ubuntu nemělo potřebné testovací
  prostředí a nepodařilo se v něm přeložit síťové jméno pro stažení runtime.
- Skutečné A/B měření a neprázdné přetoky nebyly na tomto účtu dostupné k ověření.
- Konvence časové značky (začátek/konec intervalu) není v návodu jednoznačná.
  Implementace používá začátek intervalu; před ostrým použitím porovnejte s Distribucí24.
- Samotná instalace přes HACS ještě nebyla ověřena. Repozitář je připravený
  pro vlastní zdroj `davfiala/hacs-egd-distribuce24`.

## Doporučené přejímací ověření v HA

1. Nainstalovat do testovací instance HA s Recorderem.
2. Přidat integraci, vybrat pouze jedno z více dostupných míst a ověřit vytvořená zařízení.
3. Ověřit nabídku historických statistik v panelu Energie a porovnat denní součet s portálem.
4. Restartovat HA a ověřit, že se součet nezvětšil opakovaným importem.
5. Ověřit odpolední aktualizaci, chybové přihlášení a obnovu přístupu.

Samotné jednotkové testy používají pro koordinátor náhrady úložiště a Recorderu;
nepotvrzují plnou kompatibilitu se všemi verzemi Home Assistantu.
