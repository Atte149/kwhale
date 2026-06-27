"""Artist name transliteration and normalization.

Detects transliterated artist names (Splin, Kino, Bi-2, etc.) and maps them
to their canonical (Cyrillic) form. Also provides reverse lookup for searching
streaming services with both forms.

The mapping is built from:
  1. A static dictionary of well-known transliterations.
  2. The `artist_aliases` table (user-managed, populated by the normalization task).
  3. A runtime transliteration algorithm (ICAO style) as a fallback.
"""
from __future__ import annotations

import os
import re
import unicodedata

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Static mapping of well-known transliterations ─────────────────────────────
# Format: latin -> cyrillic (canonical)
STATIC_ALIASES: dict[str, str] = {
    "splin": "Сплин",
    "splean": "Сплин",
    "kino": "Кино",
    "bi-2": "Би-2",
    "bi2": "Би-2",
    "ddt": "ДДТ",
    "nautilus pompilius": "Наутилус Помпилиус",
    "nautilus": "Наутилус Помпилиус",
    "alisa": "Алиса",
    "aria": "Ария",
    "kukryniksy": "Кукрыниксы",
    "kukryniksy": "Кукрыниксы",
    "korol i shut": "Король и Шут",
    "korol i shut": "Король и Шут",
    "kish": "Король и Шут",
    "leningrad": "Ленинград",
    "splean": "Сплин",
    "mumiy troll": "Мумий Тролль",
    "mumiy troll": "Мумий Тролль",
    "mumu": "Мумий Тролль",
    "zemfira": "Земфира",
    "zveri": "Звери",
    "zveri": "Звери",
    "basta": "Баста",
    "noize mc": "Нойз МС",
    "noize mc": "Нойз МС",
    "odnobu": "Оксимирон",
    "oxy": "Оксимирон",
    "oxyromir": "Оксимирон",
    "loota": "Лютая",
    "loota": "Лютая",
    "gruppa skryptonite": "Скриптонит",
    "skryptonite": "Скриптонит",
    "scriptonite": "Скриптонит",
    "miya gi": "Мия Ги",
    "miya gi": "Мия Ги",
    "pharaoh": "Фараон",
    "pharaoh": "Фараон",
    "lida": "Лида",
    "lida": "Лида",
    "markul": "Маркул",
    "markul": "Маркул",
    " ATL": "ОУГ",
    "atl": "ОУГ",
    "elvira t": "Эльвира Т",
    "elvira t": "Эльвира Т",
    "nervy": "Нервы",
    "nervy": "Нервы",
    "vremya i stecla": "Время и Стекло",
    "potap i nastya": "Потап и Настя",
    "vremya i stecla": "Время и Стекло",
    "the limba": "Лимба",
    "limba": "Лимба",
    "eldzhey": "Элджей",
    "eldzhey": "Элджей",
    "elijay": "Элджей",
    "macan": "Маcan",
    "macan": "Маcan",
    "macan": "Маcan",
    "krovostok": "Кровосток",
    "krovostok": "Кровосток",
    "noize": "Нойз",
    "basta": "Баста",
    "guf": "Гуф",
    "st1m": "Стим",
    "stim": "Стим",
    "liga": "Лигалайз",
    "legalize": "Лигалайз",
    "lepsh": "Леш",
    "seryoga": "Серёга",
    "sergey": "Сергей",
    "aleksandr": "Александр",
    "aleksandra": "Александра",
    "maksim": "Максим",
    "maxim": "Максим",
    "dmitriy": "Дмитрий",
    "dmitry": "Дмитрий",
    "yuri": "Юрий",
    "yuriy": "Юрий",
    "igorr": "Игорь",
    "igor": "Игорь",
    "sergey": "Сергей",
    "sergei": "Сергей",
    "andrey": "Андрей",
    "andrei": "Андрей",
    "nikolay": "Николай",
    "nikolai": "Николай",
    "aleksey": "Алексей",
    "alexey": "Алексей",
    "alexei": "Алексей",
    "evgeny": "Евгений",
    "evgeniy": "Евгений",
    "evgeni": "Евгений",
    "artem": "Артём",
    "artiom": "Артём",
    "mikhail": "Михаил",
    "mihail": "Михаил",
    "ilya": "Илья",
    "ilya": "Илья",
    "kirill": "Кирилл",
    "kiril": "Кирилл",
    "roman": "Роман",
    "daniil": "Даниил",
    "danil": "Данил",
    "denis": "Денис",
    "pavel": "Павел",
    "pavle": "Павел",
    "anton": "Антон",
    "vladimir": "Владимир",
    "vlad": "Влад",
    "oleg": "Олег",
    "oleh": "Олег",
    "egor": "Егор",
    "ivan": "Иван",
    "pyotr": "Пётр",
    "peter": "Пётр",
    "petr": "Пётр",
    "boris": "Борис",
    "vadim": "Вадим",
    "vadym": "Вадим",
    "yuriy": "Юрий",
    "yura": "Юра",
    "kolya": "Коля",
    "nikita": "Никита",
    "gosha": "Гоша",
    "georgiy": "Георгий",
    "george": "Георгий",
    "tatyana": "Татьяна",
    "tatiana": "Татьяна",
    "olga": "Ольга",
    "olga": "Ольга",
    "elena": "Елена",
    "yelena": "Елена",
    "irina": "Ирина",
    "iryna": "Ирина",
    "natalia": "Наталья",
    "natalya": "Наталья",
    "natali": "Наталья",
    "svetlana": "Светлана",
    "marina": "Марина",
    "anna": "Анна",
    "anya": "Аня",
    "maria": "Мария",
    "mariya": "Мария",
    "masha": "Маша",
    "katya": "Катя",
    "ekaterina": "Екатерина",
    "yekaterina": "Екатерина",
    "katia": "Катя",
    "katya": "Катя",
    "vera": "Вера",
    "nadezhda": "Надежда",
    "nadya": "Надя",
    "galina": "Галина",
    "lyudmila": "Людмила",
    "ludmila": "Людмила",
    "valentina": "Валентина",
    "valeriya": "Валерия",
    "valeria": "Валерия",
    "liliya": "Лилия",
    "lilya": "Лиля",
    "yuliya": "Юлия",
    "julia": "Юлия",
    "yulia": "Юлия",
    "yulya": "Юля",
    "julya": "Юля",
    "alisa": "Алиса",
    "alysa": "Алиса",
    "oksana": "Оксана",
    "marina": "Марина",
    "sofia": "София",
    "sofiya": "София",
    "sonya": "Соня",
    "darya": "Дарья",
    "daria": "Дарья",
    "dash": "Даша",
    "dasha": "Даша",
    "lera": "Лера",
    "lera": "Лера",
    "lera": "Лера",
}


# ICAO transliteration table (GOST 7.79-2000 System B, simplified)
_CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

_LATIN_TO_CYRILLIC: dict[str, str] = {
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е",
    "zh": "ж", "z": "з", "i": "и", "y": "й", "k": "к", "l": "л",
    "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с",
    "t": "т", "u": "у", "f": "ф", "kh": "х", "ts": "ц", "ch": "ч",
    "sh": "ш", "shch": "щ", "yu": "ю", "ya": "я", "yo": "ё",
    "j": "й", "x": "кс", "q": "к", "w": "в", "h": "х",
}


def has_cyrillic(text: str) -> bool:
    """Check if text contains any Cyrillic characters."""
    return any("\u0400" <= c <= "\u04ff" for c in text)


def has_latin(text: str) -> bool:
    """Check if text contains any Latin characters."""
    return any("a" <= c.lower() <= "z" for c in text)


def transliterate_to_latin(text: str) -> str:
    """Transliterate Cyrillic text to Latin (ICAO style)."""
    result = []
    for c in text.lower():
        if c in _CYRILLIC_TO_LATIN:
            result.append(_CYRILLIC_TO_LATIN[c])
        else:
            result.append(c)
    return "".join(result)


def transliterate_to_cyrillic(text: str) -> str:
    """Transliterate Latin text to Cyrillic (reverse ICAO).

    Handles multi-letter sequences (zh, sh, ch, etc.) by trying longest match first.
    """
    result = []
    i = 0
    text_lower = text.lower()
    while i < len(text_lower):
        # Try multi-letter matches (longest first)
        matched = False
        for length in (4, 3, 2, 1):
            if i + length <= len(text_lower):
                chunk = text_lower[i:i + length]
                if chunk in _LATIN_TO_CYRILLIC:
                    # Preserve original case for first letter
                    cyr = _LATIN_TO_CYRILLIC[chunk]
                    if i < len(text) and text[i].isupper():
                        cyr = cyr.capitalize()
                    result.append(cyr)
                    i += length
                    matched = True
                    break
        if not matched:
            result.append(text[i])
            i += 1
    return "".join(result)


def normalize_artist_name(name: str) -> str:
    """Return the canonical (Cyrillic) form of an artist name.

    1. If the name is already Cyrillic, return as-is.
    2. Check static aliases dictionary.
    3. Check artist_aliases table.
    4. Fall back to algorithmic transliteration.
    """
    if not name:
        return name

    # Already Cyrillic — return as-is
    if has_cyrillic(name) and not has_latin(name):
        return name

    name_lower = name.lower().strip()

    # Check static aliases
    if name_lower in STATIC_ALIASES:
        return STATIC_ALIASES[name_lower]

    # Check database aliases
    db_alias = _lookup_alias_in_db(name)
    if db_alias:
        return db_alias

    # Algorithmic transliteration (Latin -> Cyrillic)
    if has_latin(name) and not has_cyrillic(name):
        return transliterate_to_cyrillic(name)

    return name


def get_all_forms(name: str) -> list[str]:
    """Return all known forms of an artist name (canonical + aliases).

    Used for searching streaming services with both Cyrillic and Latin forms.
    """
    if not name:
        return []

    forms: list[str] = [name]
    canonical = normalize_artist_name(name)

    if canonical != name:
        forms.append(canonical)

    # Add transliteration in the other direction
    if has_cyrillic(name):
        latin = transliterate_to_latin(name)
        if latin and latin not in forms:
            forms.append(latin)

    # Add aliases from DB
    db_aliases = _get_aliases_from_db(name)
    for alias in db_aliases:
        if alias not in forms:
            forms.append(alias)

    return forms


def _lookup_alias_in_db(name: str) -> str | None:
    """Look up canonical name for an alias in artist_aliases table."""
    if not DATABASE_URL:
        return None
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT artist_name FROM artist_aliases WHERE alias ILIKE %s LIMIT 1",
                    (name,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        print(f"_lookup_alias_in_db error: {e}")
        return None


def _get_aliases_from_db(name: str) -> list[str]:
    """Get all aliases for a canonical name from the database."""
    if not DATABASE_URL:
        return []
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT alias FROM artist_aliases WHERE artist_name ILIKE %s",
                    (name,),
                )
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"_get_aliases_from_db error: {e}")
        return []


def populate_static_aliases() -> int:
    """Insert all static aliases into the artist_aliases table.

    Returns the number of aliases inserted.
    """
    if not DATABASE_URL:
        return 0
    count = 0
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                for alias, canonical in STATIC_ALIASES.items():
                    cur.execute(
                        "INSERT INTO artist_aliases (artist_name, alias, alias_type) "
                        "VALUES (%s, %s, 'translit') "
                        "ON CONFLICT (artist_name, alias) DO NOTHING",
                        (canonical, alias),
                    )
                    count += cur.rowcount
    except Exception as e:
        print(f"populate_static_aliases error: {e}")
    return count