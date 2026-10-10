#!/usr/bin/env python3
"""장혜영의 하찮은 모험담 — 텔레그램 자동 게시 봇.

외부 라이브러리 없이 파이썬 표준 라이브러리만 사용한다.

사용법
  python bot.py            # 새 소식 확인 후 게시 (기본)
  python bot.py weekly     # 주간 정리 '이번 주 모험담' 게시
  python bot.py intro      # 고정 소개글 게시 (처음 1회)
  python bot.py backfill   # 최근 7일치 중 아직 채널에 없는 것 게시 (1회용)
  python bot.py backfill --repost   # 게시 기록과 무관하게 최근 N일치 전부 다시 게시
  python bot.py --dry-run  # 게시하지 않고 출력만
  python bot.py --dry-run --since 7   # 최근 7일치를 '새 소식'으로 간주해 미리보기

직접 찾은 소식은 두 가지로 넣는다. 둘 다 다음 실행(최대 15분 뒤)에 게시된다.
  1) 텔레그램에서 봇에게 1:1로 링크를 보낸다 (TELEGRAM_OWNER 계정만 받음).
     링크만          → 장혜영 본인 글이면 본문을 그대로(400자까지) 게시
     링크 + 한 줄    → 다른 사람 글: 한 줄을 제목으로 🤝 게시
     링크 + 긴 글    → 본문을 못 가져올 때: 붙여 넣은 글을 장혜영 글 본문으로 게시
     삭제 77         → 채널 77번 글과 주간 정리 기록을 지움 (번호 대신 t.me 링크나 원문 링크도 됨)
  2) manual.yml에 적는다. 형식은 manual.yml 머리말 참고.

환경변수
  TELEGRAM_TOKEN  봇 토큰 (필수, GitHub Secrets에 저장)
  TELEGRAM_CHAT   채널 (기본값 @janghyeyeong_quest)
  TELEGRAM_OWNER  1:1 메시지를 받을 텔레그램 사용자 ID (숫자). 비어 있으면 봇이 보낸 사람에게 ID를 알려 줌
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))
NAME = "장혜영"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
MAX_POSTS_PER_RUN = 10
UA = "Mozilla/5.0"  # 일부 사이트(일다)가 봇 표시 UA에 빈 응답을 줌

# ── 소스 ────────────────────────────────────────────────
YT = "yt:"  # 유튜브 채널 표시 (yt_items 로 가져옴)
SHOWS = [
    # id, 표시 이름, 소스 (yt:채널ID 또는 RSS URL)
    # 고정 출연 (2026.10 기준)
    ("cpbc", "cpbc 김준일의 시사천국", "https://podcast.cpbc.co.kr/open/feed.xml"),  # 수 18시
    ("hk", "한국일보 이슈전파사", YT + "UC1aS5CRRDrN6CmR2VcpmetA"),                  # 목 15시
    ("sbs", "SBS 최선호의 뉴스직격", YT + "UCLv3v82YNNsa8EsxrcPMjGQ"),               # 목 17시
    ("cbs", "CBS 주말뉴스쇼", YT + "UC4Aa3OPkMenwTANpf0oWVRQ"),                      # 토 8시
    # 비정기·임시 출연 (이름이 나올 때만 게시되므로 남겨 둠)
    ("jtbc", "JTBC 장르만 여의도", YT + "UCsqWTNmoaNPvsfeCgaD7BpQ"),
    ("bbs", "BBS 아침저널", YT + "UCq1jDKl5IRN_n7xhc2xFNJA"),
]
# 정기 기고: 제목에 이름이 없어 매체 사이트에서 직접 읽는다.
# (Google 뉴스 검색은 GitHub 서버에서 결과가 비어 나오는 경우가 있어 쓰지 않음)
HANI_SERIES = "https://www.hani.co.kr/arti/SERIES/3236/home01.html"   # 장혜영의 읽고사니즘
ILDARO_SEARCH = ("https://www.ildaro.com/search.html?submit=submit&search_and=1&search_exec=all"
                 "&search_section=all&news_order=1&search=" + urllib.parse.quote("장혜영"))
# 선택: YouTube Data API 키. 있으면 가장 안정적 (RSS는 간헐적으로 404).
YT_API_KEY = os.environ.get("YT_API_KEY", "").strip()
YT_MAX_PAGES = 2                      # 되올리기 때 늘어남
YT_UNTIL = datetime.now(timezone(timedelta(hours=9))) - timedelta(days=2)
# 홈페이지 RSS 제목 앞머리 → 방송 id (홈페이지에 나중에 올라오는 같은 방송을 거르기 위함)
HOMEPAGE_SHOW_PREFIX = {
    "[JTBC] 장르만 여의도": "jtbc",
    "[CBS] 박성태의 뉴스쇼": "cbs",
    "[BBS] 아침저널": "bbs",
    "[가톨릭평화방송] 김준일의 시사천국": "cpbc",
}
HOMEPAGE_RSS = "https://mangwonjeongx.org/rss"
JHY_YOUTUBE = YT + "UCGdB-lgTS2sOhJIxgP550qw"

# 뉴스: 동명이인을 줄이기 위해 정치 맥락 단어를 함께 검색하고, 제목에 이름이 있는 기사만 쓴다.
NEWS_QUERY = '"장혜영" (의원 OR 정의당 OR 망원정 OR 진보 OR 정치) when:2d'
NEWS_URL = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(NEWS_QUERY)
            + "&hl=ko&gl=KR&ceid=KR:ko")
# 블로그·포털 재게시는 제외 (원 매체 기사만)
NEWS_EXCLUDE_SOURCES = ("브런치", "네이트", "v.daum.net", "다음", "네이버 블로그", "티스토리",
                        "public25.com")
NEWS_EXCLUDE_WORDS = ("[부고]", "[인사]", "부고", "모친상", "부친상", "장모상", "빙부상")

# 직접 찾은 소식: 자동으로 못 잡는 글(인스타 등)을 사람이 적어 두면 봇이 게시한다.
MANUAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual.yml")
MANUAL_FIELDS = ("링크", "종류", "출처", "제목", "설명")
MANUAL_KIND = {"연대": "solidarity", "방송": "show", "칼럼": "column", "기사": "news",
               "영상": "video", "망원정x": "home"}
# 공유 추적값 (인스타·스레드는 쿼리 전체를 지움)
TRACKING_PARAMS = re.compile(r"^(utm_\w+|stkn|igsh|igshid|fbclid|mibextid|si)$")
FB_KEEP_PARAMS = ("story_fbid", "id", "fbid", "v", "set")  # 옛 형식 페이스북 주소는 쿼리가 곧 글 주소

# 텔레그램 1:1 입력
OWNER_ID = os.environ.get("TELEGRAM_OWNER", "").strip()
# 장혜영 본인 계정 (주소·아이디 기준, 소문자)
JHY_HANDLES = {"facebook": {"serious.hyeyeong"}, "instagram": {"serious_sister"}, "x": {"janghyeyeong"}}
SNS_NAME = {"facebook": "페이스북", "instagram": "인스타그램", "x": "트위터"}  # 채널에서는 'X' 대신 '트위터'로 표기
SNS_MAX = 400   # 본인 글은 이 길이까지만 옮기고 나머지는 원문 링크로
MEMO_MAX = 80   # 링크 뒤 글이 이보다 길면 메모가 아니라 '붙여 넣은 본문'으로 본다
FB_UA = "facebookexternalhit/1.1"  # 페이스북은 링크 미리보기용 UA에 본문을 내줌


# ── 공통 유틸 ───────────────────────────────────────────
def fetch(url, timeout=25, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "ko-KR,ko;q=0.9"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # 한 소스가 실패해도 나머지는 계속 진행
            if attempt == tries - 1:
                print(f"[warn] fetch 실패 {url[:80]}: {e}", file=sys.stderr)
                return None
            time.sleep(3)


def strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).astimezone(KST)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST)
    except Exception:
        return None


def parse_feed(url, tries=3):
    """RSS/Atom을 [{title, link, date, text}]로."""
    if url.startswith(YT):
        return yt_items(url[len(YT):])
    raw = fetch(url, tries=tries)
    if not raw:
        return []
    try:
        return _parse_xml(raw)
    except ET.ParseError as e:
        print(f"[warn] XML 파싱 실패 {url[:80]}: {e}", file=sys.stderr)
        return []


def _parse_xml(raw):
    root = strip_ns(ET.fromstring(raw))
    out = []
    for it in root.iter("item"):  # RSS
        enc = it.find("enclosure")
        link = (it.findtext("link") or "").strip()
        if not link and enc is not None:  # 팟캐스트: 링크가 없으면 음원 주소
            link = enc.get("url", "")
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "link": link,
            "date": parse_date(it.findtext("pubDate")),
            "text": (it.findtext("description") or ""),
            "source": (it.findtext("source") or "").strip(),
        })
    for it in root.iter("entry"):  # Atom (YouTube)
        link_el = it.find("link")
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "link": link_el.get("href") if link_el is not None else "",
            "date": parse_date(it.findtext("published")),
            "text": " ".join(filter(None, [it.findtext(".//description")])),
            "source": "",
        })
    return out


def yt_items(cid):
    """유튜브 채널 최신 영상. API 키 → RSS → 채널 페이지 순으로 시도."""
    if YT_API_KEY:
        # 한국일보·SBS 라디오처럼 하루 수십 개를 올리는 채널이 있어 여러 쪽(50개씩)을 읽는다.
        # 평소엔 최근 2쪽(100개), 되올리기 땐 기간 시작일에 닿을 때까지 최대 YT_MAX_PAGES쪽.
        out, token, ok = [], "", False
        for _ in range(YT_MAX_PAGES):
            url = ("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=50"
                   f"&playlistId=UU{cid[2:]}&key={YT_API_KEY}" + (f"&pageToken={token}" if token else ""))
            raw = fetch(url)
            if not raw:
                break
            ok = True
            data = json.loads(raw)
            for x in data.get("items", []):
                sn = x["snippet"]
                vid = sn.get("resourceId", {}).get("videoId", "")
                out.append({"title": sn.get("title", ""), "text": sn.get("description", ""),
                            "link": f"https://www.youtube.com/watch?v={vid}",
                            "date": parse_date(sn.get("publishedAt")), "source": ""})
            token = data.get("nextPageToken", "")
            oldest = min((o["date"] for o in out if o["date"]), default=None)
            if not token or (oldest and oldest < YT_UNTIL):
                break
        if ok:
            return out
    raw = fetch("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid, tries=4)
    if raw:
        try:
            return _parse_xml(raw)
        except ET.ParseError:
            pass
    # 최후 수단: 채널 동영상 페이지 (제목만 있음, 설명·날짜 없음)
    raw = fetch(f"https://www.youtube.com/channel/{cid}/videos?hl=ko&gl=KR")
    if not raw:
        return []
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", raw.decode("utf-8", "ignore"))
    if not m:
        return []
    found, seen_ids = [], set()

    def walk(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                v = o["lockupViewModel"]
                t = (v.get("metadata", {}).get("lockupMetadataViewModel", {})
                     .get("title", {}).get("content"))
                if v.get("contentId") and t:
                    found.append((v["contentId"], t))
            if "videoRenderer" in o:
                v = o["videoRenderer"]
                found.append((v["videoId"], "".join(r["text"] for r in v["title"]["runs"])))
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(json.loads(m.group(1)))
    out = []
    for vid, t in found:
        if vid not in seen_ids:
            seen_ids.add(vid)
            out.append({"title": t, "text": "", "link": f"https://www.youtube.com/watch?v={vid}",
                        "date": None, "source": ""})
    # 채널 페이지는 날짜가 없어 오래된 영상을 걸러낼 수 없다. 최신 5개만 본다.
    out = out[:5]
    print(f"[info] {cid}: API·RSS 실패, 채널 페이지로 대체 (최신 {len(out)}건)", file=sys.stderr)
    return out


def hani_column():
    """한겨레 '장혜영의 읽고사니즘' 연재 페이지 (페이지에 심긴 JSON에서 제목·날짜를 읽음)."""
    raw = fetch(HANI_SERIES)
    if not raw:
        return []
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  raw.decode("utf-8", "ignore"), re.S)
    if not m:
        print("[warn] 한겨레 연재 페이지 구조가 바뀜", file=sys.stderr)
        return []
    found = []

    def walk(o):
        if isinstance(o, dict):
            if "title" in o and "createDate" in o and "url" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(m.group(1)))
    out, seen_ids = [], set()
    for o in found:
        if o["url"] in seen_ids:
            continue
        seen_ids.add(o["url"])
        try:
            d = datetime.strptime(o["createDate"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        except Exception:
            continue  # 날짜를 모르면 오래된 글일 수 있으므로 건너뜀
        t = html.unescape(o["title"])
        out.append({"key": f"news:{norm(t)}", "kind": "column",
                    "label": "한겨레 토요판 · 장혜영의 읽고사니즘", "title": t,
                    "link": "https://www.hani.co.kr" + o["url"], "date": d, "nkey": norm(t)})
    print(f"[소스] 한겨레 읽고사니즘: {len(out)}건", file=sys.stderr)
    return out


def ildaro_items():
    """일다 사이트 검색 '장혜영'. 필자가 장혜영이면 칼럼, 아니면 기사."""
    raw = fetch(ILDARO_SEARCH)
    if not raw:
        return []
    s = raw.decode("utf-8", "ignore")
    out = []
    for b in s.split("search_result_list_box")[1:]:
        a = re.search(r"href='/(\d+)'>([^<]+)</a></dt>", b)
        n = re.search(r"class='name'>([^<]*)<", b)
        d = re.search(r"(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2})", b)
        if not (a and d):
            continue
        t = html.unescape(a.group(2)).strip().lstrip("\ufeff")
        when = datetime(*map(int, d.groups()), tzinfo=KST)
        is_col = n and NAME in n.group(1)
        out.append({"key": f"news:{norm(t)}", "kind": "column" if is_col else "news",
                    "label": "일다 정치칼럼" if is_col else "일다", "title": t,
                    "link": f"https://www.ildaro.com/{a.group(1)}", "date": when, "nkey": norm(t)})
    print(f"[소스] 일다: {len(out)}건", file=sys.stderr)
    return out


def norm(s):
    """제목 비교용 정규화: [매체] 머리·(날짜) 꼬리·' - 매체' 꼬리 제거 후 한글/영숫자만."""
    s = html.unescape(s)
    s = re.sub(r"^\s*\[[^\]]*\]\s*", "", s)
    s = re.sub(r"\s*\(\d{4}\.\d{1,2}\.\d{1,2}\)\s*$", "", s)
    s = re.sub(r"\s+-\s+[^-]+$", "", s)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)[:20]


def vkey(link):
    """링크 비교용 키. 유튜브는 영상 ID로 통일 (watch?v= / shorts/ 차이 제거)."""
    m = re.search(r"(?:v=|shorts/|youtu\.be/)([\w-]{11})", link or "")
    return "yt:" + m[1] if m else link


def clean_link(u):
    """공유 링크에 붙는 추적값 제거."""
    p = urllib.parse.urlsplit(u.strip())
    host = p.netloc.lower()
    if host.endswith(("instagram.com", "threads.net", "threads.com")):
        q = ""
    elif host.endswith("facebook.com"):
        q = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query)
                                    if k in FB_KEEP_PARAMS])
    else:
        q = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                                    if not TRACKING_PARAMS.match(k)])
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, q, ""))


def manual_items():
    """manual.yml 읽기. 외부 라이브러리 없이 읽을 수 있게 단순한 형식만 받는다:
    '- 링크: ...'로 항목 시작, '  키: 값'으로 필드, 들여쓴 줄은 앞 필드에 이어 붙임."""
    try:
        with open(MANUAL_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    entries, cur, last = [], None, None
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if line.startswith("- "):
            cur, last = {}, None
            entries.append((n, cur))
            line = "  " + line[2:]
        if cur is None:
            print(f"[warn] manual.yml {n}행: '- 링크:'로 시작하는 항목 밖의 줄이라 무시", file=sys.stderr)
            continue
        m = re.match(r"^\s*([^:\s]+)\s*:\s?(.*)$", line)
        if m and m[1] in MANUAL_FIELDS:
            last = m[1]
            cur[last] = m[2].strip()
        elif last:
            cur[last] += "\n" + s
        else:
            print(f"[warn] manual.yml {n}행: 알 수 없는 줄이라 무시", file=sys.stderr)
    out = []
    for n, x in entries:
        x = {k: re.sub(r'^(["\'])(.*)\1$', r"\2", v.strip(), flags=re.S) for k, v in x.items()}
        if not x.get("링크") or not x.get("제목"):
            print(f"[warn] manual.yml {n}행 항목: 링크·제목이 없어 건너뜀", file=sys.stderr)
            continue
        kind = MANUAL_KIND.get(x.get("종류") or "연대")
        if not kind:
            print(f"[warn] manual.yml {n}행 항목: 종류 '{x['종류']}'를 몰라 '연대'로 게시", file=sys.stderr)
            kind = "solidarity"
        link = clean_link(x["링크"])
        v = vkey(link)
        out.append({"key": "manual:" + v, "alt": "link:" + v, "manual": True, "kind": kind,
                    "label": x.get("출처") or "직접 찾은 소식", "title": x["제목"],
                    "desc": x.get("설명", ""), "link": link, "date": None, "link_text": "원문 보기"})
    print(f"[소스] manual.yml: {len(out)}건", file=sys.stderr)
    return out


# ── 텔레그램 1:1 입력 ───────────────────────────────────
def tg(method, **params):
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        return None
    data = urllib.parse.urlencode(params).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.load(r)
            return res.get("result") if res.get("ok") else None
    except Exception as ex:
        print(f"[warn] 텔레그램 {method} 실패: {ex}", file=sys.stderr)
        return None


def fetch_page(url, ua=UA):
    """(최종 주소, html). 짧은 공유 링크는 원래 주소로 풀린다."""
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.geturl(), r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[warn] 게시물 가져오기 실패 {url[:80]}: {e}", file=sys.stderr)
        return url, ""


def og(page, prop):
    for pat in (r'<meta[^>]*property="%s"[^>]*content="([^"]*)"', r'<meta[^>]*content="([^"]*)"[^>]*property="%s"'):
        m = re.search(pat.replace("%s", re.escape(prop)), page)
        if m:
            return html.unescape(m[1]).strip()
    return ""


def platform_of(url):
    h = urllib.parse.urlsplit(url).netloc.lower()
    if h.endswith(("facebook.com", "fb.me", "fb.watch")):
        return "facebook"
    if h.endswith("instagram.com"):
        return "instagram"
    if h.endswith(("x.com", "twitter.com")):
        return "x"
    return None


def fetch_post(url):
    """SNS 게시물 한 건: 작성자 아이디·이름, 본문, 게시일, 정리된 링크, 글 ID."""
    pf = platform_of(url)
    out = {"platform": pf, "author": "", "name": "", "text": "", "date": None,
           "link": clean_link(url), "id": ""}
    if pf == "x":  # 트위터는 로그인 없이 못 읽어서 무료 미리보기 서비스(fxtwitter)를 거친다
        m = re.search(r"/status(?:es)?/(\d+)", url)
        raw = fetch(f"https://api.fxtwitter.com/status/{m[1]}", tries=2) if m else None
        try:
            t = json.loads(raw)["tweet"]
            out.update(author=t["author"]["screen_name"], name=t["author"]["name"], text=t["text"],
                       date=datetime.fromtimestamp(t["created_timestamp"], KST), id=m[1],
                       link=f"https://x.com/{t['author']['screen_name']}/status/{m[1]}")
        except Exception:
            pass
        return out
    if pf not in ("facebook", "instagram"):
        return out
    final, page = fetch_page(url, FB_UA if pf == "facebook" else UA)
    if not page or "/login" in final:
        return out
    path = [x for x in urllib.parse.urlsplit(final).path.split("/") if x]
    if pf == "facebook" and (not path or path[0] in ("share", "login", "l.php", "watch", "story.php")):
        return out  # 공유 링크가 원래 주소로 안 풀림 = 못 가져온 것
    desc = og(page, "og:description")
    if pf == "facebook":
        out.update(author=path[0] if path else "", name=og(page, "og:title"), text=desc,
                   link=clean_link(final))
        m = re.search(r"/(\d{8,})/?$", og(page, "og:url"))
        out["id"] = m[1] if m else ""
        if out["name"] in ("Facebook", "") and not desc:
            out["text"] = ""
    else:  # 인스타: '좋아요 N개 ... - 아이디 on 날짜: "본문".'
        # 언어 설정에 따라 ' on 날짜' / ' - 날짜' / '님, 날짜'로 달라진다
        m = re.match(r'^.*? - ([\w.]+)(?: on | - |님, )([^:"]+): "(.*)"\.?\s*$', desc, re.S)
        if m:
            out.update(author=m[1], name=m[1], text=m[3].strip())
            for f in ("%B %d, %Y", "%Y년 %m월 %d일"):
                try:
                    out["date"] = datetime.strptime(m[2].strip(), f).replace(tzinfo=KST)
                    break
                except ValueError:
                    pass
        if len(path) >= 2 and path[0] in ("p", "reel", "tv"):
            out["id"] = path[1]
    return out


def clip(t, n=SNS_MAX):
    t = t.strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    i = max(cut.rfind("\n"), cut.rfind(" "))
    if i > n * 0.6:  # 단어·줄 중간에서 자르지 않도록
        cut = cut[:i]
    return cut.rstrip() + "…"


INBOX_HELP = ("링크를 보내 주세요.\n"
              "· 링크만 → 장혜영 본인 글이면 본문을 그대로 게시\n"
              "· 링크 + 한 줄 메모 → 다른 사람 글을 메모를 제목으로 게시\n"
              "· 링크 + 전문 → 본문을 못 가져올 때, 붙여 넣은 글을 장혜영 글로 게시\n"
              "· 삭제 77 → 채널 77번 글과 주간 정리 기록에서 삭제 (번호 대신 원문 링크도 됨)")


def delete_post(st, arg, dry):
    """채널 글 삭제 + 게시 기록에서 제거. 지운 기록은 deleted에 남겨 상태 병합 때 되살아나지 않게 한다.
    본 것(seen)·manual_done은 그대로 두므로 같은 글이 다시 올라가지 않는다. 답장 문구를 돌려준다."""
    m = re.search(r"t\.me/(?:s/)?\w+/(\d+)", arg) or re.fullmatch(r"(\d+)", arg)
    mid = int(m[1]) if m else None
    if mid is not None:
        hits = [x for x in st["log"] if x.get("mid") == mid]
    else:
        def match(link):
            return [x for x in st["log"] if x["link"] == link or vkey(x["link"]) == vkey(link)]
        hits = match(clean_link(arg))
        if not hits and platform_of(arg):  # 공유 링크면 원래 주소로 풀어서 다시 찾기
            hits = match(fetch_post(arg)["link"])
        mid = next((x["mid"] for x in hits if x.get("mid")), None)
    channel_msg = ""
    if mid is not None:
        if dry:
            print(f"[dry-run] 채널 {mid}번 삭제")
            ok = True
        else:
            ok = tg("deleteMessage", chat_id=os.environ.get("TELEGRAM_CHAT", "@janghyeyeong_quest"),
                    message_id=mid)
        channel_msg = (f"채널 {mid}번 글을 지웠어요." if ok else
                       f"채널 {mid}번 글은 봇이 못 지웠어요 (이미 지웠거나 48시간이 지남). 필요하면 텔레그램에서 직접 지워 주세요.")
    if not hits:
        return (channel_msg + "\n" if channel_msg else "") + \
            "게시 기록에서는 못 찾았어요. 예전 글이면 번호 대신 원문 링크로 보내 주세요: 삭제 https://…"
    gone = {(x["link"], x["at"]) for x in hits}
    st["log"] = [x for x in st["log"] if (x["link"], x["at"]) not in gone]
    st.setdefault("deleted", []).extend({"link": l, "at": a} for l, a in gone)
    return (channel_msg + "\n" if channel_msg else "") + \
        f"주간 정리 기록에서 지웠어요: {hits[0]['title'][:40]}"


def inbox_items(st, dry):
    """봇에게 온 1:1 메시지를 게시 항목으로. 처리한 메시지는 tg_offset으로 넘긴다."""
    res = tg("getUpdates", offset=st.get("tg_offset", 0), timeout=0, allowed_updates='["message"]')
    if not res:
        return []

    def reply(chat, text):
        send(text, dry, chat=chat)

    items, last = [], None
    for u in res:
        last = u["update_id"]
        msg = u.get("message") or {}
        if msg.get("chat", {}).get("type") != "private":
            continue
        uid, chat = str(msg.get("from", {}).get("id", "")), msg["chat"]["id"]
        if not OWNER_ID:
            reply(chat, f"아직 주인이 등록되지 않은 봇이에요.\n당신의 텔레그램 ID: {uid}\n"
                        "GitHub 저장소 Settings → Secrets and variables → Actions에 "
                        "TELEGRAM_OWNER 이름으로 이 숫자를 넣으면, 이 계정의 메시지만 받아요.")
            continue
        if uid != OWNER_ID:
            continue
        text = msg.get("text") or msg.get("caption") or ""
        cmd = re.match(r"^\s*/?(?:삭제|delete)\s+(\S+)", text)
        if cmd:
            reply(chat, delete_post(st, cmd[1], dry))
            continue
        urls = [e["url"] for e in msg.get("entities", []) + msg.get("caption_entities", [])
                if e.get("type") == "text_link"] + re.findall(r"https?://\S+", text)
        if not urls:
            reply(chat, INBOX_HELP)
            continue
        url = urls[0]
        memo = text.replace(url, "").strip()
        pasted, note = (memo, "") if len(memo) > MEMO_MAX else ("", memo)
        post = fetch_post(url)
        pf = post["platform"]
        print(f"[소스] 1:1 메시지: {pf or '기타'} · {post['author'] or '작성자 모름'} · 본문 {len(post['text'])}자",
              file=sys.stderr)
        is_jhy = post["author"].lower() in JHY_HANDLES.get(pf, set()) if post["author"] else False
        base = {"key": "manual:" + (f"{pf}:{post['id']}" if post["id"] else vkey(post["link"])),
                "alt": "link:" + vkey(post["link"]), "manual": True, "desc": "",
                "link": post["link"], "date": post["date"], "reply_chat": chat, "link_text": "원문 보기"}
        if is_jhy or pasted:  # 긴 글을 붙여 보냈으면 장혜영 글 전문으로 본다
            body = post["text"] if (is_jhy and post["text"]) else pasted
            if not body:
                reply(chat, "장혜영 글인데 본문을 못 가져왔어요. 링크 뒤에 전문을 붙여 다시 보내 주세요.")
                continue
            items.append({**base, "kind": "sns", "label": f"장혜영 {SNS_NAME.get(pf, 'SNS')}",
                          "title": clip(body)})
        elif note:
            who = post["name"] or post["author"]
            items.append({**base, "kind": "solidarity", "title": note,
                          "label": f"{who} {SNS_NAME.get(pf, '')}".strip() or "직접 찾은 소식"})
        elif not post["text"]:
            reply(chat, "본문을 못 가져왔어요.\n장혜영 글이면 링크 뒤에 전문을, "
                        "다른 사람 글이면 한 줄 메모를 붙여 다시 보내 주세요.")
        else:
            reply(chat, f"{post['author']}의 글이라 소개할 한 줄 메모가 필요해요. 링크 뒤에 메모를 붙여 다시 보내 주세요.\n"
                        f"(장혜영 본인 계정이 맞다면 bot.py의 JHY_HANDLES에 '{post['author']}'를 추가해 주세요.)")
    if last is not None and not dry:
        st["tg_offset"] = last + 1
    return items


def day(d):
    return d.strftime("%Y-%m-%d") if d else ""


# ── 수집 ────────────────────────────────────────────────
def collect():
    items = []  # 각 item: key, kind, label, title, link, date, show(옵션), nkey(옵션)

    # 1) 방송: '장혜영'이 제목/설명에 있는 항목. 같은 방송·같은 날은 하나만.
    for sid, label, url in SHOWS:
        feed = parse_feed(url)
        hits = [e for e in feed if NAME in e["title"] or NAME in e["text"]]
        print(f"[소스] {label}: {len(feed)}개 중 '{NAME}' {len(hits)}건", file=sys.stderr)
        by_day = {}
        for e in hits:
            k = day(e["date"]) or e["link"]  # 날짜를 모르면(채널 페이지 대체) 영상별로
            cur = by_day.get(k)
            # 제목에 이름이 들어간 항목을 우선
            if cur is None or (NAME in e["title"] and NAME not in cur["title"]):
                by_day[k] = e
        for k, e in by_day.items():
            key = f"show:{sid}:{k}" if e["date"] else "link:" + vkey(e["link"])
            items.append({"key": key, "alt": "link:" + vkey(e["link"]),
                          "kind": "show", "label": label,
                          "title": html.unescape(e["title"]), "link": e["link"],
                          "date": e["date"], "show": sid})

    # 2) 장혜영 유튜브: 전부
    for e in parse_feed(JHY_YOUTUBE):
        items.append({"key": "video:" + vkey(e["link"]), "alt": "link:" + vkey(e["link"]),
                      "kind": "video", "label": "장혜영 유튜브",
                      "title": html.unescape(e["title"]), "link": e["link"], "date": e["date"]})

    # 3) 망원정x 홈페이지 RSS
    for e in parse_feed(HOMEPAGE_RSS):
        t = html.unescape(e["title"])
        show = next((sid for p, sid in HOMEPAGE_SHOW_PREFIX.items() if t.startswith(p)), None)
        if show:  # 방송: 방송일 키로 방송사 피드와 중복 제거
            m = re.search(r"\((\d{4})\.(\d{1,2})\.(\d{1,2})\)", t)
            bday = f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else day(e["date"])
            label = next(l for s, l, _ in SHOWS if s == show)
            bdt = datetime.strptime(bday, "%Y-%m-%d").replace(tzinfo=KST)
            items.append({"key": f"show:{show}:{bday}", "kind": "show", "label": label,
                          "title": f"{fmt_date(bdt)} 방송 출연", "link": e["link"],
                          "date": bdt, "show": show})
        elif "빅토크]" in t or "읽고사니즘" in t:  # 유튜브·한겨레 연재 소스가 이미 다룸
            continue
        elif t.startswith(("[장혜영의 편지]", "[망원정담")):  # 자체 글
            items.append({"key": f"home:{e['link']}", "kind": "home", "label": "망원정x",
                          "title": t, "link": e["link"], "date": e["date"]})
        elif t.startswith("[") or t.startswith("bbs뉴스]"):  # 언론 보도 스크랩
            m = re.match(r"^\[?\s*([^\]]+)\]", t)
            dm = re.search(r"\((\d{4})\.(\d{1,2})\.(\d{1,2})\)\s*$", t)
            if dm:  # 표시 날짜는 기사 날짜로
                e["date"] = datetime(int(dm[1]), int(dm[2]), int(dm[3]), tzinfo=KST)
            items.append({"key": f"home:{e['link']}", "kind": "news",
                          "label": (m[1].strip() if m else "언론 보도"),
                          "title": re.sub(r"\s*\(\d{4}\.\d{1,2}\.\d{1,2}\)\s*$", "",
                                          re.sub(r"^\[?[^\]]*\]\s*", "", t)),
                          "link": e["link"],
                          "date": e["date"], "nkey": norm(t)})
        else:  # 활동소식·편지·망원정담 등
            items.append({"key": f"home:{e['link']}", "kind": "home", "label": "망원정x",
                          "title": t, "link": e["link"], "date": e["date"]})

    # 4) 뉴스 키워드
    for e in parse_feed(NEWS_URL):
        t = html.unescape(e["title"])
        src = e["source"] or (t.rsplit(" - ", 1)[1] if " - " in t else "")
        if NAME not in t:
            continue
        if any(x in src for x in NEWS_EXCLUDE_SOURCES) or any(w in t for w in NEWS_EXCLUDE_WORDS):
            continue
        title = t.rsplit(" - ", 1)[0] if " - " in t else t
        items.append({"key": f"news:{norm(t)}", "kind": "news", "label": src or "언론 보도",
                      "title": title, "link": e["link"], "date": e["date"], "nkey": norm(t)})

    cnt_news = sum(1 for i in items if i["kind"] == "news" and i["key"].startswith("news:"))
    print(f"[소스] 뉴스 검색 {cnt_news}건", file=sys.stderr)

    # 5) 정기 기고 칼럼 + 일다 기사
    items.extend(hani_column())
    items.extend(ildaro_items())

    # 6) 직접 찾은 소식
    items.extend(manual_items())
    return items


# ── 메시지 ──────────────────────────────────────────────
ICON = {"show": "📺", "news": "📰", "video": "▶️", "home": "✉️", "column": "✍️", "solidarity": "🤝",
        "sns": "💬"}
LINK_TEXT = {"show": "다시 보기", "news": "기사 보기", "video": "영상 보기", "home": "자세히 보기",
             "column": "칼럼 읽기", "solidarity": "원문 보기", "sns": "원문 보기"}
WEEKDAY = "월화수목금토일"


def fmt_date(d):
    return f"{d.month}.{d.day}({WEEKDAY[d.weekday()]})" if d else ""


def render(it):
    def e(s, quote=False):
        return html.escape(s, quote=quote)
    when = f"{fmt_date(it['date'])} · " if it["date"] else ""
    desc = f"{e(it['desc'])}\n" if it.get("desc") else ""
    return (f"{ICON[it['kind']]} <b>{e(it['label'])}</b>\n"
            f"{e(it['title'])}\n"
            f"{desc}"
            f"{when}<a href=\"{e(it['link'], quote=True)}\">{it.get('link_text') or LINK_TEXT[it['kind']]}</a>")


def send(text, dry, chat=None):
    """게시 성공 시 메시지 번호를 돌려준다 (dry-run은 True). chat을 주면 그 대화방(1:1 답장)으로."""
    if dry:
        print("─" * 40 + (f" (→ {chat}에게 답장)" if chat else "") + "\n" + text)
        return True
    token = os.environ["TELEGRAM_TOKEN"]
    chat = chat or os.environ.get("TELEGRAM_CHAT", "@janghyeyeong_quest")
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.load(r)
                if res.get("ok"):
                    return res["result"]["message_id"]
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="ignore")
            print(f"[warn] 전송 실패 {err.code}: {body[:200]}", file=sys.stderr)
            if err.code == 429:
                time.sleep(int(re.search(r'"retry_after":(\d+)', body).group(1)) + 1
                           if "retry_after" in body else 30)
                continue
            return False
        except Exception as ex:
            print(f"[warn] 전송 오류: {ex}", file=sys.stderr)
        time.sleep(3)
    return False


# ── 상태 ────────────────────────────────────────────────
# 채널에서 직접 지웠지만 게시 기록에 남아 있던 글 (2026-10-06 10:34 첫 실행분, 채널 미리보기로 부재 확인).
# 불러올 때마다 기록에서 빼고 deleted에 남긴다. 한 번 반영되면 아무 일도 하지 않으므로 지우지 않아도 된다.
PURGE_AT = "2026-10-06T10:34"
PURGE_LINKS = {"https://www.youtube.com/watch?v=" + v for v in (
    "2Rmh8ZE6IOE", "HGb86TtdClA", "9X5yNumMJPs", "K4TMH7jljyY", "tSwsolEmHoM",
    "OeHJp5a0ic0", "z98Vwhl_gn4", "_TURAOSM69c", "VSjTd_2pwaQ", "HxQTszQ2jZQ")}


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return None
    gone = [x for x in st.get("log", []) if x["at"].startswith(PURGE_AT) and x["link"] in PURGE_LINKS]
    if gone:
        st["log"] = [x for x in st["log"] if x not in gone]
        st.setdefault("deleted", []).extend({"link": x["link"], "at": x["at"]} for x in gone)
    return st


def save_state(st):
    # 기록은 최근 60일만 유지
    cutoff = (datetime.now(KST) - timedelta(days=60)).isoformat()
    st["log"] = [x for x in st["log"] if x["at"] >= cutoff]
    if "deleted" in st:
        st["deleted"] = [x for x in st["deleted"] if x["at"] >= cutoff]
    st["seen"] = st["seen"][-3000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def fill_log_dates(st, items):
    """원래 날짜(date)가 없는 예전 게시 기록에 수집 결과의 날짜·키를 채운다. 못 찾으면 그대로 둔다."""
    by = {}
    for i in items:
        if i["date"]:
            by.setdefault(i["link"], i)
            by.setdefault(vkey(i["link"]), i)
    for x in st["log"]:
        if x.get("date"):
            continue
        i = by.get(x["link"]) or by.get(vkey(x["link"]))
        if i:
            x["date"], x["key"] = i["date"].isoformat(), i["key"]


# ── 실행 ────────────────────────────────────────────────
def run_update(dry, since_days=None, backfill=False, repost=False):
    """backfill=True: 최근 since_days일치를 '본 것' 여부와 무관하게 다시 게시하고 상태에 합친다."""
    st = load_state()
    items = collect()
    first_run = st is None
    if first_run:
        st = {"seen": [], "nkeys": [], "log": []}
    fill_log_dates(st, items)
    seen, nkeys = set(st["seen"]), set(st["nkeys"])
    manual_done = set(st.get("manual_done", []))  # seen은 3000건에서 잘리므로 따로 보관
    orig_seen, orig_nkeys = set(seen), set(nkeys)

    if since_days is not None:  # 최근 N일치를 새 소식으로 간주 (미리보기·되올리기)
        cutoff = datetime.now(KST) - timedelta(days=since_days)
        seen, nkeys, first_run = set(), set(), False
        if backfill:  # 날짜를 모르는 항목은 오래된 것일 수 있으므로 제외
            items = [i for i in items if i["date"] and i["date"] >= cutoff]
        else:
            items = [i for i in items if not i["date"] or i["date"] >= cutoff]

    if first_run:  # 첫 실행: 과거 소식을 한꺼번에 쏟아내지 않도록 '본 것'으로만 기록
        st["seen"] = sorted({i["key"] for i in items} | {i["alt"] for i in items if i.get("alt")})
        st["nkeys"] = sorted({i["nkey"] for i in items if i.get("nkey")})
        if not dry:
            save_state(st)
        print(f"첫 실행: 기존 항목 {len(st['seen'])}건을 기록만 하고 게시하지 않음")
        return

    if since_days is None:  # 평소 실행 때만 1:1 메시지를 읽는다 (미리보기·되올리기는 제외)
        items.extend(inbox_items(st, dry))

    # 오래된 항목은 게시하지 않음 (피드가 복구되며 옛 영상이 뒤늦게 잡히는 경우 대비).
    # 홈페이지는 방송을 최대 1주 늦게 올리므로 여유를 10일로 둔다.
    # 되올리기(backfill)는 지정한 기간을 그대로 쓴다.
    too_old = datetime.now(KST) - timedelta(days=since_days if backfill else 10)
    # 직접 넣은 소식은 날짜가 오래돼도 게시한다.
    items = [i for i in items if i.get("manual") or not i["date"] or i["date"] >= too_old]
    if backfill and not repost:  # 이미 채널에 올라간 항목(게시 기록)은 다시 올리지 않음
        logged = {x["link"] for x in st["log"]}
        items = [i for i in items if i["link"] not in logged]
    if backfill and repost and not dry:  # 다시 올릴 항목의 옛 게시 기록은 지움 (주간 정리 중복 방지)
        relinks = {i["link"] for i in items}
        st["log"] = [x for x in st["log"] if x["link"] not in relinks]

    new, batch_keys = [], set()
    for it in sorted(items, key=lambda x: x["date"] or datetime.min.replace(tzinfo=KST)):
        if (it["key"] in seen or it["key"] in batch_keys or it.get("alt") in seen
                or it["key"] in manual_done):
            if it.get("reply_chat"):
                send("이미 채널에 올라간 글이에요.", dry, chat=it["reply_chat"])
            continue
        if it.get("nkey") and any(it["nkey"][:15] == k[:15] for k in nkeys):  # 같은 기사 중복
            seen.add(it["key"])
            continue
        new.append(it)
        batch_keys.add(it["key"])
        if it.get("nkey"):
            nkeys.add(it["nkey"])

    posted = 0
    cap = 30 if backfill else MAX_POSTS_PER_RUN
    # 직접 넣은 소식은 한도와 무관하게 모두 게시 (1:1 메시지는 다시 읽지 않으므로)
    to_post = [i for i in new if i.get("manual")] + [i for i in new if not i.get("manual")][:cap]
    channel = os.environ.get("TELEGRAM_CHAT", "@janghyeyeong_quest").lstrip("@")
    for it in to_post:
        mid = send(render(it), dry)
        if not mid and it.get("reply_chat"):
            send("채널 게시에 실패했어요. 잠시 뒤 다시 보내 주세요.", dry, chat=it["reply_chat"])
        if mid:
            if it.get("reply_chat"):
                send(f"게시했어요 → https://t.me/{channel}/{mid}\n잘못 올렸으면: 삭제 {mid}"
                     if mid is not True else "게시했어요",
                     dry, chat=it["reply_chat"])
            seen.add(it["key"])
            if it.get("alt"):
                seen.add(it["alt"])
            if it.get("manual"):
                manual_done.add(it["key"])
            st["log"].append({"at": datetime.now(KST).isoformat(), "kind": it["kind"],
                              "label": it["label"], "title": it["title"], "link": it["link"],
                              "date": it["date"].isoformat() if it["date"] else None, "key": it["key"],
                              **({"mid": mid} if mid is not True else {})})
            posted += 1
            time.sleep(0 if dry else 3)
    print(f"새 항목 {len(new)}건 중 {posted}건 게시" + (" (dry-run)" if dry else ""))
    if backfill:  # 기존 기록에 합침 (지우지 않음). 이번에 본 항목은 모두 '본 것'으로.
        seen |= orig_seen | {i["key"] for i in items} | {i["alt"] for i in items if i.get("alt")}
        nkeys |= orig_nkeys
    st["seen"], st["nkeys"] = sorted(seen), sorted(nkeys)
    st["manual_done"] = sorted(manual_done)
    if not dry and (since_days is None or backfill):
        save_state(st)


TITLE_DATE = re.compile(r"(?:\((\d{4})\.)?(\d{1,2})[./](\d{1,2})\s*\([월화수목금토일]\)")
WEEKLY_BUDGET = 3500  # 메시지 하나의 HTML 원문 길이 상한 (텔레그램 한도 4096자보다 여유 있게)

# 주간 정리 배너와 홍보 꼬리말
WEEKLY_BANNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekly_banner.jpg")  # bot.py와 같은 위치
CAPTION_MAX = 1024  # 사진 설명글 한도 (태그를 뺀 글자 수, UTF-16 기준)
CHANNEL_URL = "https://t.me/janghyeyeong_quest"
# 매주 주간 정리 끝에 붙는 공유 요청
PROMO_SHARE = ("📣 <i>\"꿈은 동료들과 함께 이루는 것이다.\"</i> — 플람메\n"
               "이 모험담을 함께 읽을 동료를 찾아요. 장혜영 소식이 궁금할 만한 사람에게 이 글을 그대로 전달해 주세요.\n"
               f"👉 {CHANNEL_URL}")


def visible_len(h):
    """HTML 태그를 뺀, 텔레그램이 세는 글자 수 (UTF-16 단위)."""
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    return len(t.encode("utf-16-le")) // 2


def send_photo(path, caption, dry, silent=False):
    """사진 게시. caption이 없으면 사진만. 실패하면 False (주간 정리는 글만이라도 나가게 함)."""
    if dry:
        print("─" * 40 + f"\n[사진] {os.path.basename(path)}" + (" (알림 없음)" if silent else "")
              + (f"\n{caption}" if caption else ""))
        return True
    token = os.environ["TELEGRAM_TOKEN"]
    chat = os.environ.get("TELEGRAM_CHAT", "@janghyeyeong_quest")
    fields = {"chat_id": chat}
    if caption:
        fields.update(caption=caption, parse_mode="HTML")
    if silent:
        fields["disable_notification"] = "true"
    boundary = "----jhyquest" + str(int(time.time() * 1000))
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    with open(path, "rb") as f:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                 f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
        body += f.read() + f"\r\n--{boundary}--\r\n".encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
                if res.get("ok"):
                    return res["result"]["message_id"]
        except urllib.error.HTTPError as err:
            msg = err.read().decode(errors="ignore")
            print(f"[warn] 사진 전송 실패 {err.code}: {msg[:200]}", file=sys.stderr)
            if err.code == 429:
                time.sleep(int(re.search(r'"retry_after":(\d+)', msg).group(1)) + 1
                           if "retry_after" in msg else 30)
                continue
            return False
        except Exception as ex:
            print(f"[warn] 사진 전송 오류: {ex}", file=sys.stderr)
        time.sleep(3)
    return False


def log_date(x, now):
    """게시 기록의 원래 날짜. 기록에 없으면 제목의 'M/D(요일)'·'M.D(요일)', 그래도 없으면 게시 시각."""
    if x.get("date"):
        return datetime.fromisoformat(x["date"]).astimezone(KST)
    m = TITLE_DATE.search(x["title"])
    if m:
        d = datetime(now.year, int(m[2]), int(m[3]), tzinfo=KST)
        return d.replace(year=d.year - 1) if d > now + timedelta(days=1) else d
    return datetime.fromisoformat(x["at"]).astimezone(KST)


def weekly_title(x):
    t = next((ln.strip() for ln in x["title"].splitlines() if ln.strip()), "")
    t = re.sub(r"^\d{1,2}[./]\d{1,2}\s*\([월화수목금토일]\)\s*", "", t)  # 앞머리 날짜는 따로 표시함
    t = re.sub(r"\s+", " ", t)
    if x["kind"] == "show" and (not t or t == "방송 출연"):
        return x["label"]
    return t if len(t) <= 60 else t[:59] + "…"


def weekly_dedup(xs):
    """같은 방송(방송명+방송일)·같은 링크는 하나만. 방송은 구체적인 제목을 가진 쪽을 남긴다."""
    out = {}
    for x in xs:
        k = (f"show:{x['label']}:{day(x['_d'])}" if x["kind"] == "show" else vkey(x["link"]))
        cur = out.get(k)
        if cur is None or (cur["title"].endswith("방송 출연") and not x["title"].endswith("방송 출연")):
            out[k] = x
    return list(out.values())


def run_weekly(dry):
    st = load_state() or {"log": []}
    now = datetime.now(KST)
    start = now - timedelta(days=7)
    last = st.get("weekly_last") or start.isoformat()  # 지난 주간 정리 시각 (첫 정리면 7일 전)
    for x in st["log"]:
        x["_d"] = log_date(x, now)
    # 이번 주: 원래 날짜가 최근 7일 안
    week = weekly_dedup([x for x in st["log"] if start <= x["_d"] <= now + timedelta(days=1)])
    # 늦게 들어온 소식: 지난 정리 뒤에 게시됐지만 날짜는 그 전 주 (홈페이지는 방송을 최대 1주 늦게 올림)
    late = weekly_dedup([x for x in st["log"] if x["at"] > last
                         and start - timedelta(days=7) <= x["_d"] < start])
    if not week and not late:
        print("이번 주 게시물 없음: 주간 정리 생략")
        return
    e = html.escape
    order = [("show", "방송"), ("sns", "SNS"), ("solidarity", "연대"), ("column", "칼럼"), ("news", "기사"), ("video", "영상"),
             ("home", "망원정x")]

    def line(x):
        pre = f"{x['label']} · " if x["kind"] == "show" and weekly_title(x) != x["label"] else ""
        return (f"· {fmt_date(x['_d'])} {e(pre)}"
                f"<a href=\"{e(x['link'], quote=True)}\">{e(weekly_title(x), quote=False)}</a>")

    blocks = []  # (머리 줄, [항목 줄])
    for kind, name in order:
        xs = sorted((x for x in week if x["kind"] == kind), key=lambda x: x["_d"])
        if xs:
            blocks.append((f"{ICON[kind]} <b>{name} {len(xs)}건</b>", [line(x) for x in xs]))
    if late:
        blocks.append(("🕰 <b>지난주 소식, 늦게 올라온 것</b>",
                       [f"{ICON[x['kind']]} " + line(x)[2:] for x in sorted(late, key=lambda x: x["_d"])]))

    head = f"🗓 <b>이번 주 모험담</b> ({start.month}.{start.day}~{now.month}.{now.day})"
    tail = "거대한 전투보다 오늘 지킨 작은 일들. 다음 주에도 계속됩니다.\n\n" + PROMO_SHARE
    msgs, cur = [], [head, ""]
    for title, rows in blocks:
        cur.append(title)
        for r in rows:
            if len("\n".join(cur + [r])) > WEEKLY_BUDGET:  # 넘치면 끊고 다음 메시지에서 이어 씀
                started = cur[-1] != title  # 이 묶음의 항목이 이미 앞 메시지에 들어갔는지
                if not started:
                    cur.pop()
                msgs.append(cur)
                cur = [head, "", title + (" (이어서)" if started else "")]
            cur.append(r)
        cur.append("")
    if len("\n".join(cur + [tail])) > WEEKLY_BUDGET:
        msgs.append(cur)
        cur = [head, ""]
    cur.append(tail)
    msgs.append(cur)
    if len(msgs) > 1:
        for n, m in enumerate(msgs, 1):
            m[0] = f"{head} ({n}/{len(msgs)})"
    texts = []
    for m in msgs:
        while m and m[-1] == "":
            m.pop()
        texts.append("\n".join(m))
    # 배너: 정리가 짧으면 사진 + 설명글 한 개로, 길면 배너를 알림 없이 먼저 올리고 글을 이어 보냄
    if os.path.exists(WEEKLY_BANNER):
        if len(texts) == 1 and visible_len(texts[0]) <= CAPTION_MAX:
            if send_photo(WEEKLY_BANNER, texts[0], dry):
                texts = []
        else:
            send_photo(WEEKLY_BANNER, None, dry, silent=True)
            time.sleep(0 if dry else 2)
    else:
        print(f"[warn] 배너 파일 없음: {WEEKLY_BANNER} (글만 게시)", file=sys.stderr)
    for t in texts:
        send(t, dry)
        time.sleep(0 if dry else 3)
    if not dry:
        for x in st["log"]:
            x.pop("_d", None)
        st["weekly_last"] = now.isoformat()
        save_state(st)


INTRO = """🗡 <b>장혜영의 하찮은 모험담</b>

장혜영이 어디서 무슨 말을 했는지, 올라오는 대로 모아 전해요.

📡 <b>이런 곳에서 가져와요</b>
망원정x 홈페이지 · 장혜영 유튜브(빅토크) · 출연 방송(cpbc 김준일의 시사천국, 한국일보 이슈전파사, SBS 최선호의 뉴스직격, CBS 주말뉴스쇼) · 칼럼(한겨레 토요판, 일다) · '장혜영' 언론 보도


🤳 <b>직접 골라 가져와요</b> (그래서 놓치는 게 있어요 ㅠㅠ)
장혜영 SNS·연대 소식


🗓 매주 일요일엔 한 주를 묶은 <b>'이번 주 모험담'</b>을 올려요.

거대한 전투 대신 아침 라디오 한 꼭지, 칼럼 한 편, 동네 모임 한 번.
그런 일들이 쌓여 세상은 분명히 변하고 있다고 믿으며, 부지런히 기록합니다.

"모두가 무사히 할머니, 할아버지가 될 수 있는 사회"를 향해.
지는 것에 익숙해지지 맙시다.

※ 지지자가 운영하는 비공식 채널입니다."""


def merge_state(other_path):
    """다른 실행이 먼저 저장한 state.json과 합친다 (본 것·게시 기록 모두 합집합)."""
    mine = load_state() or {"seen": [], "nkeys": [], "log": []}
    with open(other_path, encoding="utf-8") as f:
        other = json.load(f)
    mine["seen"] = sorted(set(mine["seen"]) | set(other.get("seen", [])))
    mine["nkeys"] = sorted(set(mine["nkeys"]) | set(other.get("nkeys", [])))
    mine["manual_done"] = sorted(set(mine.get("manual_done", [])) | set(other.get("manual_done", [])))
    mine["tg_offset"] = max(mine.get("tg_offset", 0), other.get("tg_offset", 0))
    wl = [v for v in (mine.get("weekly_last"), other.get("weekly_last")) if v]
    if wl:
        mine["weekly_last"] = max(wl)
    dels = {(x["link"], x["at"]): x for x in other.get("deleted", []) + mine.get("deleted", [])}
    logs = {}
    for x in other.get("log", []) + mine["log"]:  # 같은 기록이면 원래 날짜가 채워진 쪽을 남김
        k = (x["link"], x["at"])
        if k not in logs or x.get("date") or not logs[k].get("date"):
            logs[k] = x
    mine["log"] = sorted((v for k, v in logs.items() if k not in dels), key=lambda x: x["at"])
    mine["deleted"] = sorted(dels.values(), key=lambda x: x["at"])
    save_state(mine)
    print(f"상태 병합: 본 것 {len(mine['seen'])}건, 게시 기록 {len(mine['log'])}건")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["merge-state"]:
        merge_state(args[1])
        sys.exit(0)
    dry = "--dry-run" in args
    since = int(args[args.index("--since") + 1]) if "--since" in args else None
    if "backfill" in args:  # 최근 7일치 되올리기 (Actions 수동 실행 mode=backfill)
        YT_MAX_PAGES = 8
        YT_UNTIL = datetime.now(KST) - timedelta(days=(since if since is not None else 7))
        run_update(dry, since if since is not None else 7, backfill=True,
                   repost="--repost" in args)
    elif "intro" in args:  # 고정 소개글 1회 게시 (Actions 수동 실행 mode=intro)
        send(INTRO, dry)
    elif "weekly" in args:
        run_weekly(dry)
    else:
        run_update(dry, since)
