# Studio One AI Bridge

[English](README.md) | 한국어

AI 어시스턴트(Claude, ChatGPT, Codex, Gemini CLI 등 [MCP](https://modelcontextprotocol.io) 클라이언트)가
**PreSonus Studio One 5**를 읽고 조작하게 해 줍니다. 곡 목록과 요약 보기, 트랙 추가, 노트 쓰기, 악기와
이펙트 불러오기, 볼륨·팬·플러그인 파라미터 조정, 센드·버스·오토메이션·섹션 정리까지 말로 시킬 수 있습니다.

> 비공식 프로젝트입니다. PreSonus, Fender와 관계없습니다. [DISCLAIMER.md](DISCLAIMER.md)를 보세요.

**Studio One 5.5.2, macOS(Apple Silicon/arm64)에서만 테스트했습니다.** 다른 Studio One 버전, Intel 맥,
Windows는 테스트하지 않았습니다. 역공학으로 알아낸 동작 위에 만든 실험이라 거친 부분이 있습니다
([docs/re-findings.md](docs/re-findings.md)).

## 어떤 AI 앱으로 쓸까

| 앱 | 설치 | 비고 |
|---|---|---|
| **Claude Desktop** (채팅 포함) | `.mcpb` 더블클릭 | 가장 쉬움. 계정·터널 필요 없음 |
| **ChatGPT** (웹, 데스크톱 앱) | 명령 한 줄 + 커넥터 설정 | Plus/Pro 필요. [가이드](docs/chatgpt-guide.md) |
| **Codex, Claude Code, Gemini CLI, Cursor, LM Studio, VS Code** | 체크아웃 또는 Claude 확장에서 `connect` | 아래 "체크아웃으로 설치" |

## 동작 방식

```
AI 클라이언트 ──MCP (stdio)──► MCP 서버 (Python, 표준 라이브러리만)
                                ├─ song_*, library_*   디스크의 파일 읽기
                                └─ daw_*               메일함에 작업 파일 쓰기 ──┐
                                                                                ▼
                                      ~/Music/Studio One 5/AIBridge/{inbox,outbox}
                                                                                ▲
Studio One ── AI Bridge 확장 (JS) ── "AI Bridge - Run Jobs" ─────────────────────┘
      ▲
      └── 가상 MIDI 포트 "AI Bridge" (CC 119, 채널 16) ── MCP 서버가 띄우는 작은 MIDI 데몬이 소유
```

- **오프라인 도구** (`song_*`, `library_*`, `audio_analyze`)는 Python만 있으면 됩니다. 저장된 `.song`
  파일, 플러그인 목록, Sound Set 파일 이름을 읽기만 합니다. 예외는 `song_write_cc`, `song_add_loop`,
  `song_write_chords`, `song_set_time_signatures`, `song_write_markers`, `song_add_send`,
  `song_write_automation`, `song_write_sections`입니다. Studio One 스크립트 API로는 만들 수 없는 것들이라
  파일에 직접 씁니다. 곡이 열려 있으면 저장 → 닫기 → 쓰기 → 다시 열기를 스스로 합니다(약 8초, 실행 취소
  기록은 사라짐). 업데이트한 뒤에는 setup을 다시 실행해야 닫기 단계가 동작합니다.
- **실시간 도구** (`daw_*`)는 Studio One에 AI Bridge 확장이 설치되어 있어야 합니다. 서버가 메일함 폴더에
  작업 파일을 넣고 가상 MIDI 버튼을 누르면, 컨트롤 서피스가 그 버튼을 확장의 "Run Jobs" 명령에 연결해서
  열린 곡에서 작업을 실행하고 결과를 돌려씁니다.

## 요구 사항

- macOS의 Studio One 5 (5.5.2에서 테스트)
- Python 3.9 이상 (macOS 기본 `python3`도 됨). `.mcpb`와 설치 스크립트는 Python을 내장하고 있습니다.
  외부 패키지는 필요 없고, 오디오 분석(선택)에만 `numpy`가 필요합니다.
- MCP 클라이언트: Claude Desktop, ChatGPT, Codex, Claude Code 등

## 빠른 시작: Claude Desktop

1. [Releases](https://github.com/torigood/studio-one-ai-bridge/releases)에서
   `studio-one-ai-bridge-<버전>.mcpb`를 받아 더블클릭하고 **Install**을 누릅니다.
   직접 빌드하려면 `python3 packaging/build_mcpb.py`를 실행하세요(`dist/`에 생김).
2. 곡을 저장하고 Studio One을 끈 다음 Claude에게 "Studio One 설정해줘"라고 말합니다. `bridge_setup`이
   확장, 컨트롤 서피스, 장치 등록을 설치합니다.
3. Studio One을 켜고 곡을 연 다음 "피아노 트랙 하나 추가해줘"처럼 말하면 됩니다.

확장에 Python(arm64, Intel)이 들어 있어서 따로 설치할 것이 없습니다. 확장 설정에서 Studio One 앱 경로와
사용자 데이터 폴더(Studio One의 환경설정 → 위치)를 바꿀 수 있습니다.

## ChatGPT (채팅 앱)

**단계별 안내: [docs/chatgpt-guide.md](docs/chatgpt-guide.md)**

Studio One을 끈 상태에서 터미널에 붙여넣으세요. 릴리스(Python 내장), Studio One 쪽 설치, 백그라운드
공유까지 한 번에 하고, 커넥터 URL을 클립보드에 복사해 줍니다.

```bash
curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh
```

`studio-one-bridge` 명령이 생깁니다 (`studio-one-bridge share --status`, `studio-one-bridge doctor`).
삭제는 같은 명령 끝에 `sh -s -- --uninstall`을 붙이면 됩니다.

**왜 ChatGPT만 단계가 더 있나:** Claude Desktop과 Codex는 내 맥에서 서버를 직접 실행합니다. ChatGPT는
OpenAI 서버에서 도구를 호출하기 때문에 공개 HTTPS 주소가 필요합니다. `share`가 서버를 127.0.0.1에서
HTTP로 띄우고 그 앞에 터널을 붙입니다.

- **ngrok (무료 계정, 권장):** 주소가 고정되어 ChatGPT 커넥터를 한 번만 만들면 됩니다.
- **Cloudflare 퀵 터널 (계정 없음):** 시작할 때마다 주소가 바뀝니다.

체크아웃에서 직접 쓰는 경우:

```bash
python3 server/run.py share                                       # 이 터미널에서 공유 (Ctrl+C로 중지)
python3 server/run.py share --set-ngrok-token                     # ngrok authtoken 저장 (키체인)
python3 server/run.py share --ngrok-domain <이름>.ngrok-free.app --enable   # 고정 주소로 백그라운드 공유
python3 server/run.py share --status                              # 켜짐/꺼짐, 커넥터 URL
python3 server/run.py share --disable                             # 중지 (로그인 시에도)
```

ChatGPT(Plus/Pro)에서: 설정 → 앱 및 커넥터 → 고급 → 개발자 모드 켜기 → 만들기. 이름 `Studio One`, 서버
URL은 커넥터 URL, 인증은 **없음**. 채팅에서 켜고 "Studio One 연결 확인해줘"라고 말해 보세요.

Claude에게 "ChatGPT랑 Studio One 공유해줘" / "공유 꺼줘"라고 해도 됩니다 (`bridge_share`). 백그라운드
공유는 서버 코드가 바뀌면(업데이트, `git pull`) 20초 안에 같은 주소로 스스로 다시 시작합니다.

> **보안:** 공유가 켜져 있는 동안 커넥터 URL을 아는 사람은 누구나 Studio One을 조작하고 곡을 읽을 수
> 있습니다. URL을 비공개로 두고, 유출되면 `share --new-token`을 실행하세요.

## 체크아웃으로 설치 (모든 MCP 클라이언트)

### 1. 코드 받기

```bash
git clone https://github.com/torigood/studio-one-ai-bridge.git
cd studio-one-ai-bridge
python3 -m unittest discover -s tests
```

### 2. Studio One 쪽 설치

먼저 Studio One을 끄세요.

```bash
python3 server/run.py setup            # 미리 보기 (아무것도 안 바꿈)
python3 server/run.py setup --apply    # 실제 설치
```

`--apply` 없이는 무엇을 쓸지 보여주기만 합니다. 건드리는 파일은
`~/Library/Application Support/studio-one-ai-bridge/backups/`에 백업하고, Studio One이 켜져 있으면
실행하지 않으며, Studio One 앱 자체는 수정하지 않습니다. 설치하는 것:

- 확장: `~/Library/Application Support/PreSonus/Studio One 5/Extensions/local.s1aibridge/`
- "AI Bridge" 컨트롤 서피스: `.../Studio One 5/User Devices/AI Bridge/`
- 서피스를 "AI Bridge" MIDI 포트에 연결하는 장치 항목: `.../Studio One 5/MusicDevices.settings`
  (옵션 → 외부 장치 → 추가에서 하는 것과 같음)
- **내** Studio One 설치에서 만든 명령 카탈로그:
  `~/Library/Application Support/studio-one-ai-bridge/commands.json` (`commands_search`용, 레포에는 없음).
  `--no-catalog`로 건너뛸 수 있습니다.

그리고 "AI Bridge" 포트를 소유하는 MIDI 데몬을 띄웁니다.

언제든 상태 점검 (Studio One이 켜져 있고 곡이 열려 있으면 브리지에 핑도 보냄):

```bash
python3 server/run.py doctor
```

3단계에서 MCP 서버를 등록한 뒤에는 AI가 `bridge_doctor`, `bridge_setup` 도구로 대신 해줄 수도 있습니다.

### 3. AI 클라이언트에 MCP 서버 등록

```bash
python3 server/run.py connect --list           # 찾은 앱 보기
python3 server/run.py connect codex --apply    # 등록 (--apply 없으면 미리 보기)
```

앱: `claude-code`, `codex` (ChatGPT 요금제에 포함), `gemini` (Gemini CLI), `cursor`, `lmstudio`,
`vscode` (GitHub Copilot 에이전트 모드), `claude-desktop` (`.mcpb`를 안 쓸 때만). 해제는
`--remove --apply`. Claude Desktop 확장에서 "Codex도 연결해줘"라고 해도 됩니다 (`bridge_connect_client`).

직접 등록하려면, Claude Code:

```bash
claude mcp add studio-one -- python3 /path/to/studio-one-ai-bridge/server/run.py
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "studio-one": {
      "command": "python3",
      "args": ["/path/to/studio-one-ai-bridge/server/run.py"]
    }
  }
}
```

OpenAI Agents SDK 등 다른 방법은 [README.md](README.md#3-register-the-mcp-server-with-your-ai-client)를 보세요.

### 4. Studio One 실행

Studio One을 켜고 곡을 연 다음 AI에게 `daw_ping`을 시켜 보세요. 장치 설정은 필요 없습니다. setup이
컨트롤 서피스를 등록했고, MIDI 데몬이 고정 CoreMIDI ID로 **AI Bridge** 포트를 유지해서 AI 앱이나 데몬이
다시 시작해도 Studio One이 다시 찾습니다.

`daw_ping`이 응답하지 않으면 `python3 server/run.py doctor`를 실행하세요. 직접 장치를 추가하려면
**Studio One → 옵션 → 외부 장치 → 추가...**, 폴더 **local**, **AI Bridge**, **받기: AI Bridge**.

MIDI 트리거가 없으면 실시간 요청마다 **트랙 → AI Bridge - Run Jobs**를 직접 실행해야 합니다.
가상 포트를 끄려면 `S1BRIDGE_MIDI=0`을 설정하세요.

### 선택: 루프 길이 색인, 오디오 분석, VST3 파라미터 이름

[README.md](README.md#optional-loop-length-index-for-song_add_loop)의 "Optional" 절을 보세요.
요약하면:

- `song_add_loop`는 루프 길이 색인이 필요합니다 (`python3 -m s1bridge.loops fill/harvest`).
- `audio_analyze`는 numpy가 필요합니다 (`python3 -m venv .venv && .venv/bin/pip install numpy`).
- `audio_melody`(오디오의 보컬·솔로 라인을 곡 마디 위의 노트로 변환)는 분석 팩이 필요합니다:
  `studio-one-ai-bridge analysis-setup`. CREPE 신경망 음정 추적(MIT)을 ONNX Runtime으로 돌립니다
  (Apple Silicon은 Core ML, 오디오 1초당 약 0.3초). 설치 위치는
  `~/Library/Application Support/studio-one-ai-bridge/analysis/`(약 90 MB)로 앱 폴더 밖이라 업데이트해도 유지됩니다.
  겹친 보컬(더블링·화음)은 Spotify Basic Pitch 모델(Apache 2.0, `server/s1bridge/models/`에 NOTICE와 포함)로 확인해서,
  더 크게 들리는 옥타브로 노트를 옮기고(`octave_from`), 같이 울리는 음은 `also`, 두 성부 이상 겹친 구간은 `layered`로 표시합니다.
  분석은 낮은 CPU 우선순위로 돌아 재생을 방해하지 않고, 결과는 파일·구간별로 `analysis/cache/`에 캐시됩니다.
  개인 엔진: `analysis/engines/`에 `ENGINES = [...]`를 정의한 `.py`를 넣으면 `engine` 인자로 쓸 수 있고,
  레포에 들어가지 않으므로 재배포가 안 되는 라이선스의 모델도 쓸 수 있습니다.
- VST3 파라미터 이름은 `tools/vst3dump/`를 빌드하면 읽을 수 있습니다.

### 삭제

Studio One을 끈 다음:

```bash
python3 server/run.py uninstall --apply
```

확장, 서피스, 장치 항목, 카탈로그를 지웁니다.

## 도구

| 도구 | 하는 일 |
|---|---|
| `song_list` | 저장된 곡, 최신순 |
| `song_summary` | 저장된 곡의 템포, 마커, 트랙, 파트, 믹서 상태 |
| `song_track_notes` | 저장된 곡에서 트랙 하나의 노트 |
| `song_analyze` | 트랙별 키·음역과 오디오 분석 (편곡용) |
| `song_write_cc` | 저장된 곡의 파트에 MIDI CC(모듈레이션, 익스프레션 등) 쓰기. 열려 있으면 자동 저장·닫기·다시 열기, 원본은 먼저 백업 |
| `song_add_loop` | Sound Set `.audioloop`를 오디오 트랙에 원하는 길이로 반복 배치. 루프 길이 색인 필요 |
| `audio_analyze` | WAV 파일의 레벨, 스펙트럼 균형, 템포·키 추정 (numpy) |
| `song_check_clashes` | 보컬·코드 트랙 기준 편곡 검사: 보컬이나 파트끼리의 반음·단9도 충돌, 코드 밖 지속음(어보이드 노트), 보컬 음역을 가리는 파트, 같은 리듬만 치는 파트. 보컬은 오디오 트랙(`audio_melody`로 분석) 또는 멜로디 트랙 |
| `audio_melody` | 오디오의 보컬·솔로 라인을 곡 마디 위 노트로 추출, 인스트루먼트 트랙에 기록 가능 (분석 팩) |
| `library_instruments`, `library_effects` | 설치된 플러그인과 클래스 ID |
| `library_plugin_params` | PreSonus 기본 플러그인의 파라미터 ID (프리셋에서) |
| `library_loops` | 설치된 Sound Set의 루프 (색인 후 길이·BPM 포함) |
| `commands_search` | 명령 카탈로그 검색 |
| `bridge_doctor` | Studio One 쪽 설치를 하나씩 점검하고 고치는 방법 안내 |
| `bridge_setup` | Studio One 쪽 설치·업데이트 (`setup --apply`와 같음, Studio One을 꺼야 함) |
| `bridge_connect_client` | 다른 AI 앱에 이 서버 등록 (Codex, Claude Code, Gemini CLI, Cursor, LM Studio, VS Code) |
| `bridge_share` | ChatGPT 채팅 앱과 공유 켜기·끄기, 커넥터 URL 보기 |
| `daw_status`, `daw_ping` | 브리지 상태. 설치된 브리지와 서버 버전이 다르면 경고 |
| `daw_list_tracks`, `daw_list_mixer`, `daw_list_instruments` | 열린 곡의 트랙, 믹서 채널, 악기 |
| `daw_add_track`, `daw_insert_notes` | 트랙 추가, 노트가 든 파트 쓰기 |
| `daw_rename_track`, `daw_duplicate_track`, `daw_delete_track` | 이름 바꾸기(악기 채널 포함), 파트·클립까지 복제, 삭제 |
| `daw_read_notes`, `daw_edit_notes` | 열린 곡의 노트 읽기, 바꾸기·추가·삭제 |
| `daw_read_chords`, `song_write_chords` | 코드 트랙 (실시간 읽기, 오프라인 쓰기) |
| `daw_transport_info`, `daw_set_tempo`, `daw_set_position`, `song_set_time_signatures` | 템포, 재생 위치, 박자표 |
| `daw_set_loop` | 마디·박 단위 루프 구간, 루프 켜기·끄기 |
| `song_write_markers` | 마커 트랙에 섹션 이름 (Intro, Verse, Chorus...) |
| `daw_add_send`, `daw_set_send` | 이펙트(리버브, 딜레이...)가 든 새 FX 채널과 센드, 센드 레벨·팬 |
| `song_add_send` | 이미 있는 FX 채널이나 버스로 센드 |
| `daw_add_bus`, `daw_set_output` | 채널을 새 버스로 묶기, 채널 출력을 버스·FX·Main으로 |
| `song_write_automation` | 트랙의 볼륨·팬·뮤트 오토메이션 (페이드, 라이드) |
| `song_write_sections` | 어레인저 트랙의 섹션 (Intro, Verse, Chorus...) |
| `song_set_tempo_map` | 곡 중간 템포 변화와 점점 빠르게·느리게 (리타르단도) |
| `song_add_audio` | 내 WAV/AIFF 파일(샘플, 보컬)을 오디오 트랙에 넣기. 곡의 Media 폴더로 복사 |
| `song_write_plugin_automation` | 이펙트·악기 파라미터 오토메이션 (필터 스윕 등) |
| `library_presets`, `song_load_preset` | 악기 프리셋 찾기와 불러오기 (Mai Tai, Mojito, Impact, SampleOne, .vstpreset/.aupreset 파일) |
| `daw_bounce` | 트랙을 오디오로 렌더링하고 분석 |
| `daw_list_events`, `daw_repeat_event`, `daw_edit_event`, `daw_delete_event` | 트랙의 이벤트 보기, 반복, 이동·길이·뮤트, 삭제 |
| `daw_add_instrument`, `daw_insert_effect`, `daw_list_effects`, `daw_bypass_effect`, `song_remove_effect` | 악기 불러오기, 이펙트 넣기·보기·바이패스·빼기 |
| `daw_set_channel_param` | 볼륨·팬 (악기 트랙은 악기 채널을 자동으로 찾음) |
| `daw_plugin_param`, `daw_scan_plugin_params` | 플러그인 파라미터 읽기·쓰기 |
| `daw_command` | Studio One 명령 실행 (위험한 명령은 막음) |
| `daw_transport` | 재생·정지 |
| `daw_inspect_*`, `daw_probe`, `daw_dump_host`, `daw_call` | 개발용 API 탐색 (`daw_call`은 곡을 바꿀 수 있음). `S1BRIDGE_DEV_TOOLS=1`일 때만 목록에 보임 |

설명에 `EXPERIMENTAL`이 붙은 도구는 완전히 검증되지 않은 API를 씁니다.

## 안전

- AI에게 편집을 맡기기 전에 곡을 저장하세요. 브리지 작업은 일반 Studio One 편집이라 실행 취소가 되지만,
  브리지는 지금 열린 곡에 작업합니다.
- 오프라인 도구는 파일을 읽기 전용으로 엽니다(쓰기 도구는 먼저 백업). Sound Set 내용은 복호화하거나 읽지
  않고 파일 이름만 봅니다.
- `daw_command`는 파일/닫기, 종료, 되돌리기, 새로 만들기, 열기를 거부합니다.
- `setup --apply` 없이는 Studio One 폴더에 아무것도 쓰지 않고, `--target app-scripts`를 직접 고르지 않는 한
  앱 번들은 수정하지 않습니다.
- 메일함은 사용자 폴더의 평범한 폴더라 로컬 프로세스는 누구나 작업을 넣을 수 있습니다.

## 설정

모든 경로는 [server/s1bridge/config.py](server/s1bridge/config.py)에 있고 환경 변수로 바꿀 수 있습니다:
`S1BRIDGE_APP`, `S1BRIDGE_SETTINGS_DIR`, `S1BRIDGE_USER_CONTENT`, `S1BRIDGE_SONGS`, `S1BRIDGE_SOUNDSETS`,
`S1BRIDGE_PRESETS`, `S1BRIDGE_MAILBOX`, `S1BRIDGE_DATA_DIR`, `S1BRIDGE_CATALOG`,
`S1BRIDGE_ANALYSIS_PYTHON`, `S1BRIDGE_MIDI`.

메일함은 반드시 `<Studio One 사용자 콘텐츠>/AIBridge`여야 합니다. 확장이
`local://$USERCONTENT/AIBridge`를 열기 때문입니다.

## Windows (테스트 안 함)

Windows 기본 경로를 추정해 넣었지만 실행해 본 적은 없습니다. 자동 트리거는 그대로는 동작하지 않습니다
(`server/s1bridge/midi.py`가 macOS CoreMIDI로 가상 포트를 만듦). Windows에서는
[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) 같은 가상 MIDI 포트("AI Bridge")와
채널 16에 CC 119를 보내는 코드가 필요합니다. 그 전까지는 **트랙 → AI Bridge - Run Jobs**를 직접 실행하세요.
기여 환영합니다.

## .mcpb에 들어 있는 서드파티 소프트웨어

Claude Desktop 확장과 설치 스크립트는
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)의 CPython 3.12를 포함합니다
(Python Software Foundation License, 번들 안의 `python/darwin-*/lib/python3.12/LICENSE.txt`. OpenSSL,
SQLite, zlib, libffi 같은 라이브러리는 각자의 라이선스를 따름). 이 레포 자체에는 서드파티 코드가 없습니다.

## 레포 구조

```
server/s1bridge/   MCP 서버: JSON-RPC 루프, 도구, 곡 파서, 메일함, MIDI, 설정, 공유(share.py)
server/run.py      어느 폴더에서나 쓸 수 있는 진입점
bridge/src/        Studio One 확장 소스 (JavaScript + XML)
packaging/         build_mcpb.py: Claude Desktop 확장(.mcpb, Python 포함)을 dist/에 빌드; start.sh 실행기
surface/           "AI Bridge" 컨트롤 서피스 정의
install/           install.sh: 한 줄 설치 스크립트; install.py: 예전 설치 인터페이스
re/tools/          패키지 리더와 카탈로그 추출기 (내 설치에서 실행)
docs/              역공학 노트, 설치 설계, ChatGPT 가이드
tests/             python3 -m unittest discover -s tests
```

## 라이선스

MIT, [LICENSE](LICENSE) 참고. Studio One은 해당 소유자의 상표입니다. [DISCLAIMER.md](DISCLAIMER.md)를 보세요.
