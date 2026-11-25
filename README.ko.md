# Studio One AI Bridge

[English](README.md) | 한국어

**PreSonus Studio One 5**에서 AI와 대화하며 곡을 만듭니다. Claude, ChatGPT, Codex 등
[MCP](https://modelcontextprotocol.io)를 쓰는 앱에 "Am F C G로 워킹 베이스 깔아줘", "패드가 탁해, 믹스 정리해줘"라고
말하면 열려 있는 곡에서 AI가 직접 작업합니다.

> 비공식 프로젝트입니다. PreSonus, Fender와 관계없습니다. [DISCLAIMER.md](DISCLAIMER.md)를 보세요.
> macOS(Apple Silicon)의 Studio One 5.5.2에서 테스트했습니다. 다른 버전, Intel 맥, Windows는 테스트하지 않았습니다.

## 이런 걸 시킬 수 있어요

| | 예시 |
|---|---|
| **파트 만들기** | "Am F C G로 패드랑 워킹 베이스 8마디 깔아줘" · "드럼 트랙에 트랩 비트 넣고 4마디마다 필인 넣어줘" |
| **편집** | "하이햇 퀀타이즈하고 스윙 좀 넣어줘" · "코러스 한 음 올려줘" · "1절을 33마디로 복사해줘" · "17마디에 8마디 공간 만들어줘" |
| **소리** | "따뜻한 패드 찾아서 불러와줘" (Vital 프리셋도 됨) · "보컬에 보컬 체인 걸어줘" · "킥 칠 때 베이스 눌러줘" |
| **믹스** | "믹스 점검해줘" (음량, 어떤 트랙끼리 겹치는지) · "믹스 정리해줘" (레벨, 겹치는 대역 EQ 컷) |
| **구성** | "Intro, Verse, Chorus 마커 넣어줘" · "33마디부터 90 BPM으로 느려지게" · "코드 트랙에 코드 적어줘" |
| **듣고 분석** | "이 곡 키가 뭐야?" · "부른 멜로디를 노트로 바꿔줘" · "보컬이랑 부딪히는 파트 있어?" |
| **되돌리기** | "먼저 스냅샷 찍어줘" … "스냅샷 이후 한 거 다 되돌려줘" |

저장된 곡 읽기, 설치된 악기·이펙트·프리셋·루프 찾기, 볼륨·팬·센드·플러그인 값 조정도 됩니다.
도구 전체 목록은 [개발자 가이드](docs/DEVELOPMENT.md#tools)(영어)에 있습니다.

## 설치

쓰는 앱을 고르세요.

| 앱 | 방법 | 비고 |
|---|---|---|
| **Claude Desktop** | `.mcpb` 더블클릭 | 가장 쉬움. 따로 설치할 것 없음 |
| **ChatGPT** (웹, 데스크톱 앱) | 명령 한 줄 + 커넥터 | Plus/Pro 필요. [단계별 가이드](docs/chatgpt-guide.md) |
| **Codex, Claude Code, Gemini CLI, Cursor, LM Studio, VS Code** | 명령 한 줄 + `connect` | 아래 참고 |

### Claude Desktop

1. [Releases](https://github.com/torigood/studio-one-ai-bridge/releases)에서 `studio-one-ai-bridge-<버전>.mcpb`를
   받아 더블클릭하고 **Install**을 누릅니다.
2. 곡을 저장하고 **Studio One을 끈 다음** Claude에게 "Studio One 설정해줘"라고 말합니다.
3. Studio One을 켜고 곡을 연 다음 "Studio One 연결 확인해줘"라고 말해 보세요.

### ChatGPT

Studio One을 끄고 터미널(응용 프로그램 > 유틸리티 > 터미널)에 붙여넣으세요.

```bash
curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh
```

필요한 것을 전부 설치하고(Python 포함), ChatGPT 공유 방식을 묻고(ngrok 무료 계정이면 주소가 고정됨), 커넥터 URL을
복사해 줍니다. 그다음 ChatGPT에서 설정 → 앱 및 커넥터 → 고급 → 개발자 모드 → 만들기, 이름 `Studio One`, URL 붙여넣기,
인증 **없음**. 화면마다 자세한 설명은 [가이드](docs/chatgpt-guide.md)에 있습니다.

### Codex 등 다른 앱

같은 명령을 실행하고(ChatGPT 질문은 건너뛰기), 앱을 연결하세요.

```bash
studio-one-bridge connect codex --apply     # claude-code, gemini, cursor, lmstudio, vscode도 가능
```

## 처음 써보기

1. Studio One에서 곡을 엽니다(한 번은 저장해서 파일이 있어야 합니다).
2. "Studio One 연결 확인해줘"라고 말합니다. AI가 브리지 버전을 알려주면 연결된 것입니다.
3. 작은 것부터 시켜 보세요: "피아노 트랙 추가하고 1마디에 C 메이저 코드 넣어줘."

실시간 편집은 보통의 Studio One 편집이라 **편집 → 실행 취소**가 됩니다. 몇 가지(코드, 마커, CC, 일부 센드,
오토메이션, 프리셋, 템포 변화)는 곡 파일에만 쓸 수 있어서, 곡을 저장 → 닫기 → 다시 열기를 약 8초 동안 스스로 하고
실행 취소 기록은 사라집니다. 큰 작업 전에는 **"스냅샷 찍어줘"**라고 하면 한 번에 되돌릴 수 있습니다.

## 업데이트

- **Claude Desktop**: [Releases](https://github.com/torigood/studio-one-ai-bridge/releases)에서 새 `.mcpb`를 받아 더블클릭.
- **ChatGPT와 다른 앱**: 설치 명령을 다시 실행. ChatGPT는 커넥터를 새로고침하거나 새 대화를 열면 새 도구가 보입니다.
- 업데이트가 Studio One 안쪽 부분까지 바꾸면 AI가 알려줍니다. Studio One을 끄고 "Studio One 설정해줘"라고 하세요
  (또는 `studio-one-bridge setup --apply`).

버전은 날짜입니다: `26.48.0` = 2026년 48주차의 첫 릴리스.

## 안전

- AI는 지금 열려 있는 곡에서 작업합니다. 시작 전에 저장하세요. 스냅샷과 백업으로 되돌릴 수 있습니다
  (파일 편집마다 백업이 `~/Library/Application Support/studio-one-ai-bridge/backups/`에 남습니다).
- ChatGPT 공유가 켜져 있는 동안 커넥터 URL을 아는 사람은 누구나 Studio One을 조작하고 곡을 읽을 수 있습니다.
  URL을 비공개로 두세요. `studio-one-bridge share --new-token`으로 새 URL을, `share --disable`로 공유를 끕니다.
- 설치 명령 없이는 Studio One에 아무것도 설치되지 않고, Studio One 앱 자체는 절대 바꾸지 않습니다.
- AI는 곡 닫기, 종료, 변경 내용 버리기를 할 수 없게 막혀 있습니다.

## 문제 해결

| 증상 | 해볼 것 |
|---|---|
| Studio One이 응답하지 않는다고 함 | 곡이 열려 있고 Studio One에 대화상자가 떠 있지 않은지 확인. 그다음 `studio-one-bridge doctor` (또는 "브리지 점검해줘")가 고칠 방법을 알려줍니다 |
| "Studio One을 끄고 setup을 다시 실행하라"고 함 | 저장 → Studio One 끄기 → "Studio One 설정해줘" → Studio One 켜기 |
| ChatGPT에 새 도구가 안 보임 | 커넥터 새로고침, 또는 새 대화 |
| 믹스 점검에서 어떤 트랙이 무음으로 나옴 | 바운스할 때 그 악기가 소리를 안 냄 (Studio One에서 재생해서 확인) |
| 멜로디·믹스 분석이 안 됨 | 분석 팩을 한 번 설치: `studio-one-bridge analysis-setup` (약 420 MB) |

## 한계

- 지금은 macOS의 Studio One 5만 됩니다. Windows는 가상 MIDI 포트와 테스트할 PC가 필요합니다.
- 일부 플러그인 자체 브라우저 안의 프리셋(Surge XT, BBC Symphony Orchestra)은 불러올 수 없습니다. Studio One 프리셋,
  Vital `.vital` 프리셋, `.vstpreset` / `.aupreset` 파일은 됩니다.
- 믹스 점검은 채널 페이더는 듣지만 채널 인서트 이펙트는 못 듣습니다. 그래서 자동 EQ 컷의 효과는 추정치입니다.

## 삭제

Studio One을 끄고 `curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh -s -- --uninstall`
(Studio One 쪽만 지우려면 `studio-one-bridge uninstall --apply`).

## 개발자용

동작 방식, 체크아웃에서 실행, 도구 전체, 설정, 릴리스, 테스트: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (영어).
역공학 기록: [docs/re-findings.md](docs/re-findings.md).

## 라이선스

MIT, [LICENSE](LICENSE) 참고. Studio One은 해당 소유자의 상표입니다. [DISCLAIMER.md](DISCLAIMER.md)를 보세요.
