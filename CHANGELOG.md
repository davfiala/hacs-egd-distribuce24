# Changelog

## 0.2.1 – 2026-11-13

- Druhý nezávislý signál HDO pro každé odběrné místo (např. TUV a vytápění),
  vlastní názvy, kódy a výběr relé. Druhý signál nemění tarif ani výpočet nákladů.
- Zachování původních identifikátorů senzorů. Ověřeno 86 jednotkovými testy, bez spuštění HA.

## 0.2.0 – 2026-09-24

- Stahování měření každou hodinu, vlastní názvy odběru a dodávky při nastavení.
- Volitelné HDO podle PSČ a kódu pro každé místo, výběr relé, tarif NT/VT a ceny.
- Měsíční stálé platby a historické statistiky ceny spotřeby, paušálu a celkových nákladů.
- Volba doplnit chybějící ceny aktuálními hodnotami zpět ke zvolenému datu,
  nebo historii nedoplňovat. Uložené ceny zůstávají zachované.
- Vlastní ikona integrace; místní ikony podporuje HA od verze 2026.3.
- Náklady zahrnují jen známé částky; při chybějících měřeních či rozvrhu mohou být neúplné.
- Ověřeno jednotkovými testy; instalaci a formuláře v HA ověří uživatel.

Dokumentace: TBD.
