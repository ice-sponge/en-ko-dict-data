# en-ko-dict-data

영한사전 앱용 SQLite DB를 자동으로 만드는 저장소입니다.
GitHub Actions가 매월 1일 kaikki.org에서 최신 영어 Wiktionary 데이터를 받아
`dict.db.gz`로 변환한 뒤 Releases에 올립니다.

## 앱에서 받는 주소 (항상 최신 릴리스)

```
https://github.com/<OWNER>/en-ko-dict-data/releases/latest/download/version.json
https://github.com/<OWNER>/en-ko-dict-data/releases/latest/download/dict.db.gz
```

## 로컬 테스트

```powershell
$env:LIMIT = "5000"; python scripts/build_db.py
```

| 환경변수 | 설명 |
|---|---|
| `LIMIT` | 앞 N줄만 처리 (0 = 전체) |
| `KO_ONLY` | `1`이면 한국어 번역이 있는 항목만 저장 |
| `MIN_ENTRIES` | 전체 빌드 결과가 이보다 적으면 실패 처리 |
| `SOURCE` | URL 대신 로컬 JSONL 파일 경로 |

## DB 구조

`entry(word, pos, ipa_us, ipa_uk, audio_us, audio_uk, ko, glosses)`

- `ko`: JSON 배열 `[{"w":"사전","sense":"...","roman":"sajeon","hanja":"辭典"}]`
- `glosses`: 영어 뜻풀이 JSON 배열 (최대 5개)
- `meta`: version, source, license

## 라이선스 / 출처

사전 데이터: [Wiktionary](https://en.wiktionary.org/) (CC BY-SA 4.0),
추출: [kaikki.org](https://kaikki.org/) (wiktextract).
생성된 DB도 CC BY-SA 4.0을 따르며, 앱에 출처를 표시해야 합니다.
발음 오디오 파일은 Wikimedia Commons의 파일별 라이선스를 따릅니다.
