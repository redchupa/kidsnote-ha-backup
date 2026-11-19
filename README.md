# Kidsnote HA Backup

키즈노트 알림장과 첨부 파일을 **Home Assistant OS** 기기의 로컬 저장소로 백업하는 커뮤니티 앱입니다. 앱 안에서 여러 자녀의 알림장과 생활 기록, 댓글, 사진·영상, 앨범, 공지사항과 식단을 찾아볼 수 있습니다. 준비물과 앞으로의 일정은 알림장·공지·첨부문서에서 찾아 모아 보여줍니다. 백업은 앱을 시작한 뒤 최대 30분 이내에 시작하며, 이후 기본 24시간에 최대 30분을 더한 간격으로 반복합니다.

[![Home Assistant에 저장소 추가](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fredchupa%2Fkidsnote-ha-backup)

[후원하기](#후원)

1. 위 버튼으로 저장소를 추가하거나 Home Assistant의 **설정 → 앱 → 앱 설치 → ⋮ → 저장소**에서 이 저장소의 주소를 추가합니다.
2. **Kidsnote HA Backup**을 설치합니다. 지원 아키텍처는 `amd64`와 `aarch64`입니다.
3. 앱의 **구성** 탭에서 키즈노트 자격증명을 저장한 뒤 앱을 시작합니다.
4. 앱의 **웹 UI 열기**에서 보관함을 엽니다. 첫 실행의 진행 상황은 **로그** 탭에서 확인할 수 있습니다.

이미 Kidsnote Local Backup을 설치했다면, Home Assistant에 등록한 기존 저장소를 그대로 두고 앱을 업데이트하세요. 기존 주소는 새 GitHub 주소로 연결됩니다. Home Assistant는 저장소 주소로 앱 ID를 구분하므로, 위 버튼은 처음 설치할 때 사용하세요.

2단계 인증, 저장 위치, 파일 복사 방법은 [설치 및 사용 안내](kidsnote_backup/DOCS.md)를 참고하세요. 이 앱은 Home Assistant 앱 스토어를 사용하는 Home Assistant OS용입니다.

## English

A community app for Home Assistant OS (amd64 and arm64). It copies Kidsnote reports, comments, media, albums, notices and menus to local storage. Add this repository with the button above, install **Kidsnote HA Backup**, enter your Kidsnote credentials in **Configuration**, and start the app. If you installed Kidsnote Local Backup before the rename, update it from the repository already registered in Home Assistant to keep using your existing archive. See [setup and storage details](kidsnote_backup/DOCS.md#english).

## 후원

Kidsnote HA Backup이 가족의 추억을 보관하는 데 도움이 됐다면, 토스나 PayPal로 개발을 응원해 주세요.

<table>
  <tr>
    <td align="center">
      <strong>토스</strong><br>
      <img src="https://raw.githubusercontent.com/redchupa/kr_baby_kit/main/images/toss-donation.png" alt="토스 후원 QR 코드" width="180">
    </td>
    <td align="center">
      <strong>PayPal</strong><br>
      <img src="https://raw.githubusercontent.com/redchupa/kr_baby_kit/main/images/paypal-donation.png" alt="PayPal donation QR code" width="180">
    </td>
  </tr>
</table>

**계좌이체** 토스뱅크 `1000-1261-7813` (우\*만)

Use the PayPal QR code above if you’d like to support the project from outside Korea.

## 개발

```bash
python -m pip install -r kidsnote_backup/requirements.txt
PYTHONPATH=kidsnote_backup python -m unittest discover -s tests -v
```

키즈노트 로그인, 준비물·일정 탐지와 문서 텍스트 추출 코드를 가져와 로컬 백업용으로 수정했습니다. [redchupa/kidsnote-backup](https://github.com/redchupa/kidsnote-backup)

## 라이선스 및 면책

Kidsnote HA Backup은 키즈노트나 Home Assistant와 공식 제휴하지 않은 독립 프로젝트입니다. 앱은 사용자의 Home Assistant 기기에서 키즈노트의 비공식 웹 API에 직접 요청하고, 백업 파일을 기기에 저장합니다. 키즈노트 서비스가 바뀌면 백업 기능에 영향이 생길 수 있습니다.

앱은 MIT 라이선스에 따라 **현 상태 그대로** 제공됩니다. 적용 법률이 허용하는 범위에서 개발자와 저작권자는 명시적·묵시적 보증을 제공하지 않으며, 앱 사용으로 발생한 데이터 손실, 서비스 중단 또는 기타 손해에 대해 책임을 지지 않습니다. 사용자는 키즈노트 이용약관과 데이터 접근 권한을 준수하고 백업 결과를 확인할 책임이 있습니다. [MIT 라이선스 원문](LICENSE)
