# Kidsnote HA Backup

키즈노트의 **알림장·앨범·공지·식단·일정표·투약의뢰서·귀가동의서·체온기록**과 사진·영상·첨부파일을 **Home Assistant** 기기에 자동으로 백업하고, Home Assistant 화면 안에서 바로 찾아볼 수 있는 앱입니다.

[![Home Assistant에 저장소 추가](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fredchupa%2Fkidsnote-ha-backup)

[화면 예시](#화면-예시) · [주요 기능](#주요-기능) · [설치 및 사용 방법](#설치-및-사용-방법) · [자주 묻는 질문](#자주-묻는-질문) · [후원하기](#후원)

## 화면 예시

![Kidsnote HA Backup 화면 예시](img/sample.png)

## 주요 기능

* **자동 백업**: 알림장·앨범·공지·식단·일정표·투약의뢰서·귀가동의서·체온기록과 사진·영상을 내 기기에 보관합니다.
* **달력 & 준비물**: 달력에서 하루 기록을 한눈에 보고, 알림장·공지 속 준비물을 체크리스트로 관리할 수 있습니다.
* **편한 열람 & 검색**: 사진 크게 보기, 첨부 파일 ZIP 다운로드, 동영상만 보기, 가정통신문(PDF·한글·워드) 본문 검색, 인쇄/PDF 저장을 지원합니다.
* **나만의 꾸미기**: ⚙ 설정에서 7가지 디자인 테마와 보관함 이름을 자유롭게 바꿀 수 있습니다.

## 설치 및 사용 방법

### 1. 앱 설치
1. 위 **Home Assistant에 저장소 추가** 버튼을 누르거나, Home Assistant의 **설정 → 앱 → 앱 설치 → ⋮ → 저장소**에서 `https://github.com/redchupa/kidsnote-ha-backup`을 추가합니다.
2. **Kidsnote HA Backup**을 설치합니다.

> 이미 예전 이름(`Kidsnote Local Backup`)으로 설치해 사용 중이라면, 기존 저장소를 그대로 두고 업데이트하세요.

### 2. 로그인 설정
앱의 **구성** 탭에서 **키즈노트 아이디**와 **비밀번호**만 입력하고 **저장**하세요.

* **모든 자녀를 백업하려면**: 다른 항목은 건드리지 않아도 됩니다.
* **특정 자녀만 백업하려면**: PC 브라우저에서 키즈노트에 로그인한 뒤 새 탭에서 `https://www.kidsnote.com/api/v1/me/children/`을 열어 자녀 이름 옆의 `id` 숫자만 **백업할 자녀 ID**에 입력하세요.
* **2단계 인증이나 카카오·네이버 로그인을 쓰신다면**: 아이디·비밀번호는 비워두고, 구성 화면 아래의 **사용하지 않는 선택적 구성 옵션 표시**를 켠 뒤 안내에 따라 로그인 쿠키를 입력하세요. ([자세한 방법 보기](kidsnote_backup/DOCS.md))

### 3. 앱 시작 및 보관함 열기
1. 앱을 시작하면 **30초 이내**에 첫 백업이 자동으로 시작됩니다.
2. 진행 상황은 앱의 **웹 UI 열기** 상단이나 **로그** 탭에서 확인할 수 있습니다.
3. 첫 백업이 끝나면 이후 하루에 한 번(약 24시간 간격) 자동으로 새 기록을 가져오며, 원할 때 **지금 백업** 버튼으로 바로 실행할 수도 있습니다.

## 자주 묻는 질문

* **Q. 첫 백업은 얼마나 걸리나요?**
  * 키즈노트에 무리가 가지 않도록 천천히 내려받기 때문에, 1-2년치 사진과 기록이 많으면 **2-5시간 이상** 걸릴 수 있습니다. 첫 백업만 끝나면 이후에는 새 글만 받아 보통 1-3분 만에 끝납니다.
* **Q. 백업 중인데 알림장만 보이고 앨범·공지·식단이 아직 안 떠요.**
  * `알림장 → 앨범 → 투약·귀가·체온 → 공지 → 식단 → 일정표` 순서대로 차근차근 받습니다. 웹 UI 상단이나 **로그** 탭에서 지금 어느 단계를 받고 있는지 확인하실 수 있습니다.
* **Q. 첫 백업 중에 앱을 끄거나 재부팅하면 처음부터 다시 받나요?**
  * 아니요! 이미 받은 기록은 그대로 유지되고, 다시 켜면 멈췄던 곳부터 자동으로 이어서 받습니다.
* **Q. 일정표나 체온기록 탭이 비어 있어요.**
  * 다니는 어린이집·유치원에서 해당 메뉴를 사용하지 않거나 등록된 기록이 없을 때는 비어 있는 것이 정상입니다.

자세한 2단계 인증 방법과 백업 파일 복사 방법은 [사용 안내](kidsnote_backup/DOCS.md)를 참고하세요.

## English

A community app for Home Assistant OS (`amd64` and `arm64`) that backs up Kidsnote reports, albums, notices, menus, schedules, medication requests, return-home consents, and temperature logs to local storage, with a built-in archive viewer.

* Add this repository with the button above, install **Kidsnote HA Backup**, enter your Kidsnote username and password in **Configuration**, and start the app.
* Includes 7 themes, custom archive title, integrated calendar, supply checklist, photo viewer, bulk ZIP download, video filter, document search, and automatic resume if interrupted.
* If you installed `Kidsnote Local Backup` before the rename, update from your existing repository to keep your archive. See [setup details](kidsnote_backup/DOCS.md#english).

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
