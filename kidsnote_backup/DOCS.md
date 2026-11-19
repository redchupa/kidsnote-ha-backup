# Kidsnote Local Backup

## 설정

1. 앱의 **구성** 탭에서 키즈노트 아이디와 비밀번호를 입력하고 저장합니다. 앱은 실행 중 인증 세션을 재사용하며 만료되면 아이디와 비밀번호로 다시 로그인합니다.
2. 키즈노트 계정에 **2단계 인증**이 켜져 있다면 브라우저에서 직접 로그인합니다. 개발자 도구의 쿠키 목록에서 `www.kidsnote.com`의 `sessionid` 값을 복사해 앱의 **키즈노트 세션 쿠키**에 넣습니다. 만료되면 같은 방법으로 새 값을 저장하고 앱을 재시작합니다.
3. 앱을 시작합니다. 부팅 후에도 실행하려면 **부팅 시 시작**을 사용합니다. 지금 한 번 백업하려면 앱을 시작하거나 재시작합니다.

| 구성 항목 | 기본 동작 |
| --- | --- |
| `child_ids` | 빈 목록이면 계정에 연결된 모든 자녀를 백업합니다. 숫자 ID를 지정하면 해당 자녀만 백업합니다. 첫 실행 후 백업 폴더에서 ID를 확인할 수 있습니다. |
| `interval_hours` | 첫 실행 이후 매 백업이 끝난 시점부터 24시간 기다립니다. 1~168시간으로 변경할 수 있습니다. |
| `include_albums` | 자녀별 앨범을 저장합니다. |
| `include_notices`, `include_menus` | 백업 대상 자녀의 소속 기관별 공지 및 식단을 저장합니다. |

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

Open **Configuration**, enter your Kidsnote username and password, save, and start the app. For accounts with two-step verification, sign in to Kidsnote in a browser and copy its `sessionid` cookie into `session_cookie`. Replace it and restart the app when it expires.

The app runs at startup and waits 24 hours after each run by default. Use `interval_hours` to change the wait, `child_ids` to select children (empty means all linked children), and the `include_*` settings to control albums, notices, and menus.

Files are stored in the app's `/config/backup` directory. Open the app's folder in the Samba `app_configs` share (called `addon_configs` on older versions). Each item has metadata and local attachments. Reports, albums and notices also have a `note.txt`. Check `status.json` and the app log for run results. A partial run keeps completed downloads and retries missing files on the next run.
