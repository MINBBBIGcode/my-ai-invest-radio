# 원본 시스템에서 가져온 제작 방식

이 문서는 Grok Bot에서 방송을 수정할 사람을 위한 지도다. 원본의 README와 아래 스크립트의 역할을 기준으로 정리했다. 이 패키지는 독립 실행형이며 원본 저장소를 설치할 필요가 없다.

| 제작 단계 | 원본에서 볼 파일 | Grok Bot 이식판 |
|---|---|---|
| 회차 시작 | `scripts/register_daily_task.ps1`, `scripts/run_daily_notebooklm.ps1` | Grok Bot 루틴이 방송 시간대의 날짜를 정하고 `radio.py run` 실행 |
| 자료 수집과 선별 | `src/ai_radio_station/pipeline.py`, `sources.py`, `ranker.py` | `prepare.py`가 사용자가 고른 피드와 마크다운에서 근거를 뽑음. 원본의 금융 매체별 가중치는 이식하지 않음 |
| 방송용 본문 | `src/ai_radio_station/notebooklm_pack.py` | `source1.md`에 이번 회차 사실과 출처를 함께 배치 |
| 청취자 기준 | `source2_projection_contract.py`, `portfolio_target_profile.py` | 원본은 계좌 자료에서 방송에 필요한 역할과 비중만 추림. 이식판은 사용자가 방송용으로 따로 작성한 참고 메모를 `source2.md`에 반영 |
| 오늘의 연결점 | `portfolio_action_brief.py`, `notebooklm_preflight.py` | 원본은 당일 이슈와 보유 자산의 관계를 검증. 이식판은 `source3.md`에 근거와 연결해 볼 질문을 만들고, 근거가 부족한 관계는 단정하지 않음 |
| 업로드 대상 확정 | `notebooklm_episode_contract.py`, `scripts/approve_notebooklm_upload.ps1` | 날짜별 원본 세 개의 내용을 고정하고 해시를 확인. 받는 사람이 처음 설정한 자료 범위를 매일 사용 |
| 업로드와 생성 | `scripts/publish_notebooklm.ps1`, `scripts/notebooklm_automation.py` | 기존 Windows 브라우저 조작을 `radio.py`와 NotebookLM CLI로 교체. 한 날짜의 노트북과 오디오 식별자를 보존 |
| 복구와 마무리 | `scripts/diagnose_notebooklm_recovery.ps1`, `scripts/finalize_previous_notebooklm_audio.ps1`, `scripts/finalize_notebooklm_audio.py`, `scripts/download_notebooklm_audio.py` | 저장한 회차에서 이어받아 같은 오디오를 다운로드. 실제 오디오 스트림과 길이를 검사한 뒤 링크 전달 |

## 같은 원리로 다른 방송 만들기

과학 방송이라면 첫 자료는 연구 발표의 사실과 날짜, 두 번째는 청취자의 배경 지식과 관심 분야, 세 번째는 “이 결과는 사람에게도 확인됐는가?” 같은 질문이 될 수 있다. 투자 방송이라면 첫 자료는 공시나 시장 발표, 두 번째는 ‘단기채’, ‘성장주’ 같은 방송용 자산 역할, 세 번째는 그날의 발표가 그 역할과 연결되는지 묻는 질문으로 구성한다. 숫자를 쓸 때는 근거 자료의 기준일과 단위를 함께 제공한다.

원본에는 한국 ETF 탐색, 계좌 비중 계산, 과거 회차와의 중복 주제 검사, Obsidian 보관, Telegram 알림 및 음성 받아쓰기 검사도 있다. 이식판의 기본 범위는 개인이 고른 자료로 방송을 만들고 재생 가능한 결과를 받는 데까지다. 금융 기능을 추가할 때는 수신자 자신의 데이터 제공처, 시세 기준일, 계산 검증을 먼저 정의한다. 전체 계좌 보고서를 올리는 방식으로 대체하지 않는다.

## 운영자가 지켜야 할 세 가지

1. 회차의 근거가 부족하면 이유를 알린다. 피드의 짧은 설명을 본문 전체의 결론으로 부풀리지 않는다.
2. 한 날짜의 방송에서 업로드할 것은 확정된 세 파일이다. 설정, 보고서, 상태 파일, 계정 인증 자료는 업로드 대상에서 제외한다.
3. 생성 요청, 재생 가능한 오디오, 사용자 전달은 서로 다른 단계다. 중간에 끊기면 해당 날짜의 상태와 기존 노트북을 먼저 확인한다.
