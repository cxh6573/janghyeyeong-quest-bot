# 장혜영의 하찮은 모험담 — 텔레그램 자동 게시 봇

채널: https://t.me/janghyeyeong_quest · 봇: @janghyeyeong_quest_bot

## 하는 일

- **15분마다** 아래 소스를 확인하고 새 소식을 채널에 올립니다.
  - 📺 방송: JTBC 장르만 여의도 · CBS 박성태의 뉴스쇼 · BBS 아침저널 (유튜브), cpbc 김준일의 시사천국 (팟캐스트). 제목·설명에 '장혜영'이 있는 것만. 같은 방송·같은 날은 1건.
  - ▶️ 장혜영 유튜브: 새 영상 전부
  - ✉️ 망원정x 홈페이지 RSS: 활동소식·편지·언론 스크랩 (빅토크 영상은 유튜브와 겹쳐서 제외)
  - 📰 뉴스: Google 뉴스에서 '장혜영' + 정치 맥락 단어로 검색, 제목에 이름이 있는 기사만. 부고·인사·블로그·포털 재게시 제외
- **매주 일요일 20:00(KST)** 한 주 게시물을 묶은 '이번 주 모험담'을 올립니다.
- 홈페이지에 며칠 늦게 올라오는 방송·기사는 이미 올린 것과 겹치면 건너뜁니다.

## 설치 (한 번만, 약 10분)

1. **GitHub 가입** → 오른쪽 위 `+` → **New repository**
   - 이름 예: `janghyeyeong-quest-bot`
   - **Public** 권장. 공개 저장소는 Actions가 무료·무제한입니다. 토큰은 Secrets에 넣으므로 코드가 공개돼도 노출되지 않습니다.
   - Private로 하려면 `.github/workflows/bot.yml`의 `*/15`를 `*/30`으로 바꾸세요 (비공개는 월 2,000분 무료 한도).
2. **파일 올리기**: 저장소 화면 → **Add file → Upload files** → 압축 푼 폴더의 내용 전체(`.github` 폴더 포함)를 끌어다 놓기 → **Commit changes**
   - `.github` 폴더가 안 올라가면: **Add file → Create new file** → 파일 이름 칸에 `.github/workflows/bot.yml` 입력 → 내용 붙여넣기
3. **토큰 등록**: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `TELEGRAM_TOKEN` / Secret: 봇 토큰
4. **쓰기 권한**: **Settings → Actions → General → Workflow permissions → Read and write permissions** → Save
5. **시험 실행**: **Actions** 탭 → (처음이면 활성화 버튼) → `하찮은 모험담 봇` → **Run workflow**
   - 초록 체크가 뜨면 끝. 이후로는 자동입니다.

## 선택: YouTube API 키 (안정성 향상)

유튜브 RSS는 간헐적으로 404를 냅니다. 봇은 채널 페이지로 대신 읽지만, 이 경우 JTBC처럼 **설명란에만 패널 명단이 있는 방송은 놓칠 수 있습니다.** 무료 API 키를 넣으면 해결됩니다.

1. https://console.cloud.google.com → 프로젝트 생성 → **YouTube Data API v3** 사용 설정
2. **사용자 인증 정보 → API 키 만들기**
3. GitHub Secrets에 `YT_API_KEY` 이름으로 등록

사용량은 하루 약 400 단위로, 무료 한도(1만)의 4% 수준입니다.

## 운영 메모

- GitHub 예약 실행은 몇 분~수십 분 늦어질 수 있습니다.
- 봇이 `state.json`을 커밋하며 '이미 올린 것'을 기억합니다. 지우면 첫 실행처럼 기존 항목을 기록만 하고 다시 시작합니다.
- 소스 추가·변경은 `bot.py` 위쪽 `SHOWS`, `NEWS_QUERY` 부분만 고치면 됩니다.
- 토큰이 유출되면 @BotFather에서 `/revoke` → 새 토큰을 Secrets에 다시 등록.
