# ChatGPT에서 Studio One 쓰기

ChatGPT 채팅(웹, 데스크톱 앱)으로 Studio One을 조작하는 방법입니다. 처음 한 번 15분쯤 걸리고, 그다음부터는 ChatGPT에 말만 하면 됩니다.

> Claude Desktop을 쓴다면 이 문서는 필요 없습니다. `.mcpb`를 더블클릭해서 설치하세요 ([README](../README.md#quick-start-claude-desktop)).
> ChatGPT에 레포를 주고 "이거 어떻게 설치해?"라고 물어도 됩니다. 이 문서를 기준으로 설명해 달라고 하면 더 정확합니다.

## 왜 단계가 더 있나

Claude Desktop은 내 맥에서 브리지를 직접 실행합니다. ChatGPT는 OpenAI 서버에서 도구를 호출하기 때문에, 인터넷에서 내 맥까지 닿는 HTTPS 주소가 필요합니다. 설치 스크립트가 그 주소(터널)를 만들고, 로그인할 때마다 자동으로 켭니다.

## 0. 준비물

- macOS (Apple Silicon 또는 Intel). Windows는 아직 지원하지 않습니다.
- Studio One 5
- ChatGPT **Plus 또는 Pro** 이상. 직접 만든 커넥터는 유료 요금제의 Developer mode에서만 됩니다.
- ngrok 무료 계정 (권장, 고정 주소용). [ngrok.com](https://ngrok.com)에서 가입하세요. Intel 맥은 먼저 `brew install ngrok`을 실행해야 합니다.

## 1. 설치 (명령 한 줄)

**Studio One을 끈 상태에서** 터미널(응용 프로그램 > 유틸리티 > 터미널)을 열고 붙여넣으세요.

```bash
curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh
```

스크립트가 하는 일:

1. 최신 릴리스를 받아서 SHA-256을 확인하고 `~/Library/Application Support/studio-one-ai-bridge/app`에 풉니다. Python이 들어 있어서 따로 설치할 것이 없습니다.
2. `studio-one-bridge` 명령을 만듭니다 (`~/.local/bin`).
3. Studio One 쪽(확장, 컨트롤 서피스, 장치 등록)을 설치합니다. Studio One 앱 자체는 수정하지 않습니다.
4. ChatGPT 공유 방식을 묻습니다. 아래 2단계를 보세요.

## 2. 공유 방식 고르기

| 선택 | 계정 | 주소 | 추천 |
|---|---|---|---|
| 1) ngrok | 무료 가입 필요 | 고정. ChatGPT 설정을 한 번만 하면 됨 | 권장 |
| 2) Cloudflare | 필요 없음 | 재시작하거나 로그인할 때마다 바뀜. 그때마다 ChatGPT 커넥터를 다시 만들어야 함 | 잠깐 써볼 때 |
| 3) 건너뛰기 | | | Claude Desktop이나 Codex만 쓸 때 |

**ngrok을 고르면:**

1. 브라우저에 *Your Authtoken* 페이지가 열립니다. 토큰을 복사하고 Enter를 누르세요.
2. 맥 창이 뜨면 토큰을 붙여넣고 OK를 누르세요. 토큰은 맥 키체인에만 저장되고 파일에는 남지 않습니다.
3. *Domains* 페이지가 열립니다. 무료 고정 도메인(예: `이름-단어.ngrok-free.dev` 또는 `.ngrok-free.app`)을 복사해서 터미널에 붙여넣으세요. 도메인이 없으면 그 페이지에서 하나 만드세요.

끝나면 **커넥터 URL이 클립보드에 복사됩니다.** 생김새는 `https://<도메인>/<비밀값>/mcp`입니다.

## 3. 커넥터 URL 다시 보기

```bash
studio-one-bridge share --status
```

`connector URL:` 줄을 보면 됩니다. 새로 연 터미널 창에서는 바로 동작합니다.

## 4. ChatGPT에 연결 (한 번만)

1. ChatGPT **설정 > 앱 및 커넥터 (Apps & Connectors) > 고급 (Advanced) > 개발자 모드 (Developer mode)**를 켭니다.
2. 같은 화면에서 **만들기 (Create)**를 누릅니다.
   - 이름: `Studio One`
   - MCP 서버 URL: 2단계에서 복사한 커넥터 URL
   - 인증 (Authentication): **없음 (No authentication)**
3. 경고 문구를 확인하고 만듭니다.

> 메뉴 이름은 ChatGPT 업데이트에 따라 바뀝니다. 위 경로가 없으면 설정에서 "Developer mode" 또는 "커넥터/Connectors/Plugins"를 찾아보세요.

## 5. 확인

1. Studio One을 켜고 곡을 하나 엽니다.
2. ChatGPT 새 채팅에서 도구 목록(+ 버튼)에서 `Studio One`을 켭니다.
3. 이렇게 말해 보세요: **"Studio One 연결 확인해줘"**
   - `daw_ping` 결과로 `pong`이 나오면 성공입니다.
4. 그다음은 그냥 말하면 됩니다. 예: "피아노 트랙 하나 만들고 C-Am-F-G 코드 4마디 넣어줘"

## 문제 해결

| 증상 | 해결 |
|---|---|
| 뭔가 안 됨 | `studio-one-bridge doctor` 실행. `ok`가 아닌 줄을 ChatGPT에 붙여넣고 물어보세요 |
| ChatGPT가 서버에 연결 못 함 | 맥이 켜져 있고 잠자기 상태가 아닌지 확인. `studio-one-bridge share --status`에서 `sharing now: yes`인지 확인 |
| Cloudflare 주소가 바뀜 | 정상 동작입니다. 새 URL로 커넥터를 다시 만들거나 ngrok으로 바꾸세요: `studio-one-bridge share --set-ngrok-token` 다음 `studio-one-bridge share --ngrok-domain <도메인> --enable` |
| 새 도구가 안 보임 | ChatGPT에서 커넥터를 새로고침하거나 새 채팅을 여세요 |
| daw_ping이 응답 없음 | Studio One에 곡이 열려 있는지 확인. Studio One을 껐다가 켜 보세요 |
| 공유를 끄고 싶음 | `studio-one-bridge share --disable` (다시 켜기: `share --enable`) |
| 업데이트 | 1단계 명령을 다시 실행하세요. 주소는 그대로이고, 백그라운드 공유가 새 코드로 다시 시작됩니다 |

## 보안

- **커넥터 URL을 아는 사람은 누구나** 공유가 켜져 있는 동안 내 Studio One을 조작하고 곡 파일을 읽을 수 있습니다. URL 안의 비밀값이 유일한 잠금장치입니다.
- URL을 다른 사람에게 보내거나 스크린샷으로 올리지 마세요.
- 유출됐다면 `studio-one-bridge share --new-token`으로 비밀값을 바꾸고, ChatGPT 커넥터를 새 URL로 다시 만드세요.
- 안 쓸 때는 `studio-one-bridge share --disable`로 끄세요.

## 삭제

```bash
curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh -s -- --uninstall
```

공유를 끄고 Studio One 쪽 설치와 앱, 명령을 지웁니다. 곡 파일과 데이터 폴더는 남깁니다. ChatGPT 커넥터는 ChatGPT 설정에서 직접 지우세요.
