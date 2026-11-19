# Kidsnote Local Backup

## 설정

1. 앱의 **구성** 탭에서 키즈노트 아이디와 비밀번호를 입력하고 저장합니다. 이 방법으로 로그인할 수 있으면 **세션 쿠키**는 비워두세요. 앱에서 로그인 세션이 만료되면 아이디와 비밀번호로 다시 로그인합니다.
2. 키즈노트 계정에 **2단계 인증**이 켜져 있다면 아래의 [세션 쿠키 넣기](#세션-쿠키-넣기) 순서를 따라 쿠키 값을 저장합니다.
3. 앱을 시작합니다. 부팅 후에도 실행하려면 **부팅 시 시작**을 사용합니다. 지금 한 번 백업하려면 앱을 시작하거나 재시작합니다.

| 구성 항목 | 기본 동작 |
| --- | --- |
| `child_ids` | 비워두면 계정에 연결된 모든 자녀를 백업합니다. 특정 자녀만 백업하려면 아래의 [자녀 ID 확인하기](#자녀-id-확인하기)를 참고하세요. |
| `interval_hours` | 첫 실행 이후 매 백업이 끝난 시점부터 24시간 기다립니다. 1~168시간으로 변경할 수 있습니다. |
| `include_albums` | 자녀별 앨범을 저장합니다. |
| `include_notices`, `include_menus` | 백업 대상 자녀의 소속 기관별 공지 및 식단을 저장합니다. |

### 자녀 ID 확인하기

자녀 ID는 키즈노트가 자녀마다 부여한 숫자입니다. 예를 들어 ID가 `1234567`이라면 **백업할 자녀 ID (숫자)** 목록에 `1234567`을 입력하세요. 자녀 이름 대신 숫자 ID를 사용하며, 여러 자녀를 선택할 때는 숫자를 한 개씩 목록에 추가합니다.

- **백업을 시작하기 전**에는 PC 브라우저에서 키즈노트에 로그인한 다음, 같은 브라우저의 새 탭에서 [내 자녀 ID 확인 페이지](https://www.kidsnote.com/api/v1/me/children/)를 엽니다. 화면에 코드처럼 보이는 텍스트가 나오면 해당 자녀의 `name`과 같은 항목에 있는 `id` 숫자를 확인하세요. `enrollment` 안의 기관 ID와 구분해 주세요.
- **백업한 뒤**에는 Home Assistant에서 **웹 UI 열기 → 알림장 → 자녀**를 눌러 이름 옆 ID를 확인할 수 있습니다. 예를 들어 `테스트 자녀 (ID 1234567)`이라고 표시됩니다.

### 세션 쿠키 넣기

PC의 Chrome 또는 Edge에서 진행합니다.

1. 평소처럼 아이디, 비밀번호, 인증번호를 입력해 로그인합니다. [키즈노트 웹 로그인](https://www.kidsnote.com/kr/login)
2. 로그인한 화면에서 `F12`를 누르거나, 페이지의 빈 곳을 우클릭해 **검사**를 선택합니다. 화면 옆이나 아래에 브라우저의 개발자 도구가 열립니다.
3. 개발자 도구의 **애플리케이션(Application)** 탭을 엽니다. 탭이 보이지 않으면 탭 줄 끝의 **»** 또는 **+** 메뉴를 확인하세요. 왼쪽에서 **저장소(Storage) → 쿠키(Cookies) → https://www.kidsnote.com**을 선택합니다.
4. 표의 **이름(Name)** 열에서 `sessionid` 행을 찾습니다. 해당 행의 **값(Value)** 칸을 두 번 클릭하고 전체 값을 선택해 복사한 뒤 `Esc`를 누릅니다. [Chrome 쿠키 화면 안내](https://developer.chrome.com/docs/devtools/application/cookies/)
5. Home Assistant의 **Kidsnote Local Backup → 구성 → 키즈노트 세션 쿠키 (sessionid)**에 복사한 값만 붙여넣고 저장합니다. 앱을 시작하세요. 이미 실행 중이거나 쿠키가 만료되어 새 값을 저장했다면 앱을 재시작합니다.

세션 쿠키는 로그인 정보이므로 본인의 Home Assistant 앱 구성에만 입력하세요.

## 보관함

앱 정보에서 **웹 UI 열기**를 누릅니다. 홈 화면에서 알림장, 앨범, 공지, 식단을 보고 자녀·기관 및 월별로 좁힐 수 있습니다. 검색은 제목·본문·댓글을 대상으로 합니다. 글 상세에서 생활 기록, 댓글, 사진·영상, 첨부 파일을 확인할 수 있습니다. 보관함을 열려면 관리자 계정과 Home Assistant Supervisor 2026.11.0 이상이 필요합니다.

- **준비물**은 최근 30일의 선생님 요청을 원문에서 찾아 보여줍니다.
- **달력**은 최근 60일의 알림장과 공지에서 날짜를 찾아 오늘부터 60일 앞까지 표시합니다. 각 일정과 요청에서 백업된 원문을 열 수 있습니다.
- PDF, HWP, HWPX, PPTX, DOCX 첨부문서의 읽을 수 있는 텍스트는 글 상세에 표시되고 준비물·일정 탐지에도 쓰입니다. 이미지로만 스캔된 문서, 잠긴 문서, 20MB 초과 문서는 원본 파일로 보관됩니다. 단어나 날짜만으로 자동 탐지하기 어려운 내용은 원문에서 확인할 수 있습니다.

## 저장 위치와 파일 복사

앱 컨테이너의 `/config/backup`에 저장합니다. Windows에서 파일을 복사하려면 Home Assistant의 Samba share 앱에서 `app_configs` 공유를 활성화하고 파일 탐색기에서 `\\homeassistant\app_configs`로 접속합니다. 호스트 이름이 다르면 Home Assistant의 IP 주소를 사용하세요. 이전 Samba에서는 공유 이름이 `addon_configs`일 수 있습니다. 이 공유 안의 키즈노트 앱 폴더 `<저장소-ID>_kidsnote_backup/backup`을 엽니다.

```text
backup/
  status.json
  children/<자녀-ID>/reports/<글-ID>/
  children/<자녀-ID>/albums/<앨범-ID>/
  centers/<기관-ID>/notices/<공지-ID>/
  centers/<기관-ID>/menus/<날짜>_<식단-ID>/
```

- 각 글의 `note.txt`에는 제목과 본문이 들어갑니다. `meta.json`에는 상세 기록, 댓글, 로컬 첨부 파일 이름이 들어갑니다. 공지나 앨범의 댓글 API가 제공되지 않으면 `_comments`는 `null`입니다.
- 매번 서버가 제공하는 목록과 기록을 다시 조회해 수정 내용을 반영합니다. 이미 내려받은 동일 첨부 파일은 다시 받지 않습니다. 키즈노트에서 사라진 기록과 기존 첨부는 로컬에 보존합니다.
- `status.json`의 `state`가 `partial`이면 일부 기록이나 첨부에 실패했다는 뜻입니다. 남은 기록은 계속 처리하고 다음 실행에서 다시 시도합니다. 세부 원인은 앱의 로그에 기록됩니다.
- 사진과 영상은 원본 크기로 저장되므로 첫 백업이 오래 걸리거나 저장 공간을 많이 사용할 수 있습니다. 앱 데이터가 포함되는 Home Assistant 백업은 설정한 백업 저장 위치로 전송될 수 있습니다. 백업 폴더, Samba 공유, Home Assistant 백업 파일에는 가족의 개인 데이터가 들어가므로 접근 권한을 제한하세요.

## English

Open **Configuration**, enter your Kidsnote username and password, save, and start the app. For two-step verification, sign in from Chrome or Edge on a computer. Press **F12** or right-click and select **Inspect**, then open **Application → Storage → Cookies → https://www.kidsnote.com**. Copy the **Value** from the `sessionid` row into **Kidsnote session cookie (sessionid)**, save, and restart the app. Repeat when it expires. [Kidsnote web sign-in](https://www.kidsnote.com/kr/login)

The app runs at startup and waits 24 hours after each run by default. To back up selected children, enter their numeric Kidsnote IDs (for example, `1234567`) in **Child IDs**. After signing in, look for the `id` next to each child's `name` on the linked page, or read the ID next to their name in the archive's child filter after a backup. Leave the list blank to back up every linked child. [Kidsnote child IDs](https://www.kidsnote.com/api/v1/me/children/)

Files are stored in the app's `/config/backup` directory. Open the app's folder in the Samba `app_configs` share (called `addon_configs` on older versions). Each item has metadata and local attachments. Reports, albums and notices also have a `note.txt`. Check `status.json` and the app log for run results. A partial run keeps completed downloads and retries missing files on the next run.

Open **Web UI** to browse reports, media and comments, search by text, and review supply requests and upcoming dates extracted from readable attachments and posts. The viewer requires an administrator account and Supervisor 2026.11.0 or later.
