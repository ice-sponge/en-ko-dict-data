"""
kaikki.org (English Wiktionary) JSONL -> SQLite 영한사전 DB 변환 스크립트.

원본(약 3.4GB)을 디스크에 저장하지 않고 스트리밍으로 한 줄씩 읽어 처리한다.

환경변수:
  LIMIT=5000        앞 N줄만 처리 (로컬 테스트용, 0이면 전체)
  KO_ONLY=1         한국어 번역이 있는 항목만 저장 (DB 크기 축소)
  MIN_ENTRIES=100000  전체 빌드 시 저장 항목이 이보다 적으면 실패 처리
  SOURCE=path.jsonl  URL 대신 로컬 파일에서 읽기
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request

URL = "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl"
OUT = "dict.db"
VERSION_FILE = "version.json"

LIMIT = int(os.environ.get("LIMIT", "0"))
KO_ONLY = os.environ.get("KO_ONLY", "0") == "1"
MIN_ENTRIES = int(os.environ.get("MIN_ENTRIES", "100000"))
SOURCE = os.environ.get("SOURCE")

# 앞에 있을수록 우선 (표준 발음 우선, 지역 방언은 후순위)
US_TAGS = ["General-American", "US"]
UK_TAGS = ["Received-Pronunciation", "UK", "British", "Southern-England"]


def ko_translations(e):
    """한국어 번역 목록: [{"w": 사전, "sense": 어떤 뜻의 번역인지, "roman": sajeon, "hanja": 辭典}]"""
    all_t = list(e.get("translations", []))
    for s in e.get("senses", []):
        all_t.extend(s.get("translations", []))

    result, seen = [], set()
    for t in all_t:
        if t.get("lang_code") != "ko" and t.get("code") != "ko":
            continue
        w = t.get("word")
        if not w or (w, t.get("sense")) in seen:
            continue
        seen.add((w, t.get("sense")))
        item = {"w": w}
        for src, dst in (("sense", "sense"), ("roman", "roman"), ("alt", "hanja")):
            if t.get(src):
                item[dst] = t[src]
        result.append(item)
    return result


def _rank(tags, priority):
    """tags 중 priority 목록에서 가장 앞선 순위 (없으면 None)."""
    ranks = [priority.index(t) for t in tags if t in priority]
    return min(ranks) if ranks else None


def sounds(e):
    best = {}  # key -> (rank, value)

    def offer(key, rank, value):
        if value and (key not in best or rank < best[key][0]):
            best[key] = (rank, value)

    for s in e.get("sounds", []):
        tags = s.get("tags", [])
        ipa = s.get("ipa")
        audio = s.get("mp3_url") or s.get("ogg_url")
        us, uk = _rank(tags, US_TAGS), _rank(tags, UK_TAGS)
        if us is not None:
            offer("ipa_us", us, ipa)
            offer("audio_us", us, audio)
        if uk is not None:
            offer("ipa_uk", uk, ipa)
            offer("audio_uk", uk, audio)
        if us is None and uk is None and not tags:
            offer("ipa_us", 99, ipa)  # 지역 태그 없는 IPA는 미국식 자리의 최후 대안

    return {k: best.get(k, (0, None))[1] for k in ("ipa_us", "ipa_uk", "audio_us", "audio_uk")}


# 쓰지 않는 옛/방언/비표준 변화형
MARGINAL_TAGS = {"obsolete", "archaic", "dialectal", "nonstandard", "rare", "misspelling",
                 "alternative", "table-tags", "inflection-template", "pronunciation-spelling"}

# (키, 반드시 포함할 태그, 정확히 이 태그 집합이어야 하는지)
FORM_RULES = [
    ("3sg", {"present", "singular", "third-person"}, False),  # saves
    ("past", {"past"}, True),                                  # saved
    ("pp", {"participle", "past"}, False),                     # saved
    ("ing", {"participle", "present"}, False),                 # saving
    ("pl", {"plural"}, True),                                  # dictionaries
    ("comp", {"comparative"}, True),                           # freer
    ("sup", {"superlative"}, True),                            # freest
]


def forms(e):
    """주요 변화형: {"3sg":"saves","past":"saved","pp":"saved","ing":"saving"} 등"""
    out = {}
    for f in e.get("forms", []):
        form, tags = f.get("form"), set(f.get("tags", []))
        if not form or form == "-" or tags & MARGINAL_TAGS:
            continue
        for key, need, exact in FORM_RULES:
            if key in out:
                continue
            if (tags == need) if exact else need <= tags:
                out[key] = form
    return out


def lemma(e):
    """변화형 표제어의 원형 (saved -> save).
    방언/옛 용법을 뺀 모든 뜻이 '~의 변화형'일 때만 연결한다 (cat -> catapult 같은 오연결 방지)."""
    senses = [s for s in e.get("senses", []) if not set(s.get("tags", [])) & MARGINAL_TAGS]
    if not senses or not all(s.get("form_of") for s in senses):
        return None
    w = senses[0]["form_of"][0].get("word")
    return w if w and w != e["word"] else None


def open_source():
    if SOURCE:
        return open(SOURCE, "rb")
    req = urllib.request.Request(URL, headers={"User-Agent": "dict-builder"})
    return urllib.request.urlopen(req, timeout=60)


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    con = sqlite3.connect(OUT)
    con.executescript(
        """
        CREATE TABLE entry(
            id       INTEGER PRIMARY KEY,
            word     TEXT NOT NULL,
            pos      TEXT,
            ipa_us   TEXT,
            ipa_uk   TEXT,
            audio_us TEXT,
            audio_uk TEXT,
            ko       TEXT,   -- JSON 배열: 한국어 번역
            glosses  TEXT,   -- JSON 배열: 영어 뜻풀이 (최대 5개)
            forms    TEXT,   -- JSON 객체: 변화형 {"3sg","past","pp","ing","pl","comp","sup"}
            lemma    TEXT    -- 변화형 표제어의 원형 (saved -> save)
        );
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        """
    )

    n = kept = with_ko = with_audio = 0
    with open_source() as src:
        for line in src:
            n += 1
            if LIMIT and n > LIMIT:
                break
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue  # 잘린 마지막 줄 등
            # 세부 뜻은 상위 뜻을 반복하므로 중복 제거 (순서 유지)
            glosses = list(dict.fromkeys(g for s in e.get("senses", []) for g in s.get("glosses", [])))
            if not glosses:
                continue
            ko = ko_translations(e)
            if KO_ONLY and not ko:
                continue
            snd = sounds(e)
            fm = forms(e)
            con.execute(
                "INSERT INTO entry(word,pos,ipa_us,ipa_uk,audio_us,audio_uk,ko,glosses,forms,lemma)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    e["word"], e.get("pos"),
                    snd["ipa_us"], snd["ipa_uk"], snd["audio_us"], snd["audio_uk"],
                    json.dumps(ko, ensure_ascii=False) if ko else None,
                    json.dumps(glosses[:5], ensure_ascii=False),
                    json.dumps(fm, ensure_ascii=False) if fm else None,
                    lemma(e),
                ),
            )
            kept += 1
            with_ko += bool(ko)
            with_audio += bool(snd["audio_us"] or snd["audio_uk"])
            if n % 100000 == 0:
                print(f"{n:,} lines read, {kept:,} kept", flush=True)
                con.commit()

    if not LIMIT and kept < MIN_ENTRIES:
        con.close()
        sys.exit(f"ERROR: only {kept} entries (< {MIN_ENTRIES}). kaikki format may have changed.")

    version = int(datetime.date.today().strftime("%Y%m%d"))
    con.execute("CREATE INDEX idx_word ON entry(word COLLATE NOCASE)")
    con.executemany(
        "INSERT INTO meta VALUES (?,?)",
        [("version", str(version)), ("source", URL), ("license", "CC BY-SA 4.0 (Wiktionary via kaikki.org)")],
    )
    con.commit()
    con.execute("VACUUM")
    con.close()

    with open(OUT, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    info = {
        "version": version,
        "entries": kept,
        "entries_with_ko": with_ko,
        "entries_with_audio": with_audio,
        "db_bytes": os.path.getsize(OUT),
        "sha256_db": sha,
    }
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"read {n:,} lines -> {json.dumps(info)}")


if __name__ == "__main__":
    main()
