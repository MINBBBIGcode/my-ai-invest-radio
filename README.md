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

## 무료 요금제

무료 요금제는 하루 오디오 생성 횟수와 노트북 개수가 제한됩니다. 첫 방송 메시지에는 며칠 지난 방송은 오래된 순서로 자동 삭제될 수 있고, 같은 날 여러 번 다시 만들기는 권하지 않는다는 안내를 넣습니다. 매일 루틴은 새 방송 전에 이 방송 이름(`program_name`)으로 시작하는 노트북만 날짜순으로 보고, 최근 7개를 넘는 가장 오래된 것부터 삭제합니다. 다른 이름 노트북은 절대 건드리지 않습니다. 생성 한도에 걸리면 재시도하지 않고 내일 다시 만든다고만 알립니다.

## 화면을 끄고 듣기

화면을 끄고 들으려면 NotebookLM 앱(iOS·Android)을 받아 같은 Google 계정으로 로그인하길 권한다. 방송 노트북이 앱에 그대로 있어 화면을 꺼도 계속 재생된다. 채팅에 첨부한 음성 파일은 화면을 끄면 멈출 수 있다. 이 안내도 첫 방송 메시지에 넣습니다.
