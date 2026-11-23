# 개인 맞춤 AI 투자 라디오

내 보유 종목과 관심 정보로 매일 출근길 투자 방송을 만들어 주는 Grok Bot용 패키지입니다. 자료를 모아 하루치 소스 세 개로 정리하고, NotebookLM 오디오 오버뷰로 음성을 만들어 내려받습니다.

- `START_HERE.md`: 처음 보는 사람을 위한 안내
- `GROKBOT.md`: 봇이 따라야 할 설치, 제작, 재개, 전달 절차
- `prepare.py`: 피드와 메모로 날짜별 source1~3.md를 만듭니다
- `radio.py`: NotebookLM 노트북 생성, 소스 업로드, 오디오 생성과 다운로드를 처리합니다
- `station.example.json`, `examples/`: 오프라인 연습용 가상 방송

## 봇에서 설치하기

```sh
git clone https://github.com/MINBBBIGcode/my-ai-invest-radio.git
cd my-ai-invest-radio
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Google 로그인은 사용자 본인이 직접 합니다. 이 저장소에는 계정 정보, 쿠키, 개인 자료를 올리지 마세요. 방송 내용은 정보 제공용이며 투자 권유가 아닙니다.

## 피드 묶음

`feeds/presets.json`에는 온보딩에서 고르는 정보 유형(국내 시황, 미국·글로벌 시황, 경제·금리·환율, 신규 상장, 섹터·테마)별로 검증된 피드 주소가 들어 있습니다. 주소는 그날 기사를 검색하므로 매일 새 기사가 들어옵니다. 보유 종목 피드는 `holding_feed_template`으로 사용자마다 만듭니다.
