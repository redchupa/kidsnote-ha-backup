# Kidsnote Local Backup

키즈노트 알림장과 첨부 파일을 **Home Assistant OS** 기기의 로컬 저장소로 백업하는 커뮤니티 앱입니다. 여러 자녀를 지원하며 알림장 상세 기록·댓글, 사진·영상·첨부 파일과 함께 앨범, 공지사항, 식단도 백업할 수 있습니다. 백업은 앱을 시작하면 한 번 실행하고 이후 기본 24시간 간격으로 반복합니다.

[![Home Assistant에 저장소 추가](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fredchupa%2Fhome-assistant-kidsnote-backup)

1. 위 버튼으로 저장소를 추가하거나 Home Assistant의 **설정 → 앱 → 앱 설치 → ⋮ → 저장소**에서 이 저장소의 주소를 추가합니다.
2. **Kidsnote Local Backup**을 설치합니다. 지원 아키텍처는 `amd64`와 `aarch64`입니다.
3. 앱의 **구성** 탭에서 키즈노트 자격증명을 저장한 뒤 앱을 시작합니다.
4. 앱의 **로그**에서 첫 실행 결과를 확인합니다.

2단계 인증, 저장 위치, 파일 복사 방법은 [설치 및 사용 안내](kidsnote_backup/DOCS.md)를 참고하세요. 이 앱은 Home Assistant 앱 스토어를 사용하는 Home Assistant OS용입니다.

## English

A community app for Home Assistant OS (amd64 and arm64). It copies Kidsnote reports, comments, media, albums, notices and menus to local storage. Add this repository with the button above, install **Kidsnote Local Backup**, enter your Kidsnote credentials in **Configuration**, and start the app. See [setup and storage details](kidsnote_backup/DOCS.md#english).

## 개발 및 라이선스

```bash
python -m pip install -r kidsnote_backup/requirements.txt
PYTHONPATH=kidsnote_backup python -m unittest discover -s tests -v
```

키즈노트 로그인 모듈 및 API 접근 방식은 [redchupa/kidsnote-backup](https://github.com/redchupa/kidsnote-backup)에서 가져와 로컬 백업용으로 수정했습니다. 이 프로젝트는 [MIT 라이선스](LICENSE)를 따릅니다. 키즈노트의 비공식 웹 API를 사용하므로 서비스 변경에 따라 업데이트가 필요할 수 있습니다.
