#!/usr/bin/env python3
"""장혜영의 하찮은 모험담 — 텔레그램 자동 게시 봇.

외부 라이브러리 없이 파이썬 표준 라이브러리만 사용한다.

사용법
  python bot.py            # 새 소식 확인 후 게시 (기본)
  python bot.py weekly     # 주간 정리 '이번 주 모험담' 게시
  python bot.py intro      # 고정 소개글 게시 (처음 1회)
  python bot.py --dry-run  # 게시하지 않고 출력만
  python bot.py --dry-run --since 7   # 최근 7일치를 '새 소식'으로 간주해 미리보기

환경변수
  TELEGRAM_TOKEN  봇 토큰 (필수, GitHub Secrets에 저장)
  TELEGRAM_CHAT   채널 (기본값 @janghyeyeong_quest)
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
UA = "Mozilla/5.0 (compatible; janghyeyeong-quest-bot/1.0)"

# ── 소스 ────────────────────────────────────────────────
YT = "yt:"  # 유튜브 채널 표시 (yt_items 로 가져옴)
SHOWS = [
    # id, 표시 이름, 소스 (yt:채널ID 또는 RSS URL)
    ("jtbc", "JTBC 장르만 여의도", YT + "UCsqWTNmoaNPvsfeCgaD7BpQ"),
    ("cbs", "CBS 박성태의 뉴스쇼", YT + "UC4Aa3OPkMenwTANpf0oWVRQ"),
    ("bbs", "BBS 아침저널", YT + "UCq1jDKl5IRN_n7xhc2xFNJA"),
    ("cpbc", "cpbc 김준일의 시사천국", "https://podcast.cpbc.co.kr/open/feed.xml"),
]
# 선택: YouTube Data API 키. 있으면 가장 안정적 (RSS는 간헐적으로 404).
YT_API_KEY = os.environ.get("YT_API_KEY", "").strip()
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
        url = ("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=25"
               f"&playlistId=UU{cid[2:]}&key={YT_API_KEY}")
        raw = fetch(url)
        if raw:
            out = []
            for x in json.loads(raw).get("items", []):
                sn = x["snippet"]
                vid = sn.get("resourceId", {}).get("videoId", "")
                out.append({"title": sn.get("title", ""), "text": sn.get("description", ""),
                            "link": f"https://www.youtube.com/watch?v={vid}",
                            "date": parse_date(sn.get("publishedAt")), "source": ""})
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


def day(d):
    return d.strftime("%Y-%m-%d") if d else ""


# ── 수집 ────────────────────────────────────────────────
def collect():
    items = []  # 각 item: key, kind, label, title, link, date, show(옵션), nkey(옵션)

    # 1) 방송: '장혜영'이 제목/설명에 있는 항목. 같은 방송·같은 날은 하나만.
    for sid, label, url in SHOWS:
        hits = [e for e in parse_feed(url) if NAME in e["title"] or NAME in e["text"]]
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
        items.append({"key": "video:" + vkey(e["link"]), "kind": "video", "label": "장혜영 유튜브",
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
        elif "빅토크]" in t:  # 장혜영 유튜브 피드가 이미 다룸
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
    return items


# ── 메시지 ──────────────────────────────────────────────
ICON = {"show": "📺", "news": "📰", "video": "▶️", "home": "✉️"}
LINK_TEXT = {"show": "다시 보기", "news": "기사 보기", "video": "영상 보기", "home": "자세히 보기"}
WEEKDAY = "월화수목금토일"


def fmt_date(d):
    return f"{d.month}.{d.day}({WEEKDAY[d.weekday()]})" if d else ""


def render(it):
    def e(s, quote=False):
        return html.escape(s, quote=quote)
    when = f"{fmt_date(it['date'])} · " if it["date"] else ""
    return (f"{ICON[it['kind']]} <b>{e(it['label'])}</b>\n"
            f"{e(it['title'])}\n"
            f"{when}<a href=\"{e(it['link'], quote=True)}\">{LINK_TEXT[it['kind']]}</a>")


def send(text, dry):
    if dry:
        print("─" * 40 + "\n" + text)
        return True
    token = os.environ["TELEGRAM_TOKEN"]
    chat = os.environ.get("TELEGRAM_CHAT", "@janghyeyeong_quest")
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=20) as r:
                if json.load(r).get("ok"):
                    return True
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
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(st):
    # 기록은 최근 60일만 유지
    cutoff = (datetime.now(KST) - timedelta(days=60)).isoformat()
    st["log"] = [x for x in st["log"] if x["at"] >= cutoff]
    st["seen"] = st["seen"][-3000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ── 실행 ────────────────────────────────────────────────
def run_update(dry, since_days=None):
    st = load_state()
    items = collect()
    first_run = st is None
    if first_run:
        st = {"seen": [], "nkeys": [], "log": []}
    seen, nkeys = set(st["seen"]), set(st["nkeys"])

    if since_days is not None:  # 미리보기: 최근 N일치를 새 소식으로 간주
        cutoff = datetime.now(KST) - timedelta(days=since_days)
        seen, nkeys, first_run = set(), set(), False
        items = [i for i in items if not i["date"] or i["date"] >= cutoff]

    if first_run:  # 첫 실행: 과거 소식을 한꺼번에 쏟아내지 않도록 '본 것'으로만 기록
        st["seen"] = sorted({i["key"] for i in items} | {i["alt"] for i in items if i.get("alt")})
        st["nkeys"] = sorted({i["nkey"] for i in items if i.get("nkey")})
        if not dry:
            save_state(st)
        print(f"첫 실행: 기존 항목 {len(st['seen'])}건을 기록만 하고 게시하지 않음")
        return

    # 오래된 항목은 게시하지 않음 (피드가 복구되며 옛 영상이 뒤늦게 잡히는 경우 대비).
    # 홈페이지는 방송을 최대 1주 늦게 올리므로 여유를 10일로 둔다.
    too_old = datetime.now(KST) - timedelta(days=10)
    items = [i for i in items if not i["date"] or i["date"] >= too_old]

    new, batch_keys = [], set()
    for it in sorted(items, key=lambda x: x["date"] or datetime.min.replace(tzinfo=KST)):
        if it["key"] in seen or it["key"] in batch_keys or it.get("alt") in seen:
            continue
        if it.get("nkey") and any(it["nkey"][:15] == k[:15] for k in nkeys):  # 같은 기사 중복
            seen.add(it["key"])
            continue
        new.append(it)
        batch_keys.add(it["key"])
        if it.get("nkey"):
            nkeys.add(it["nkey"])

    posted = 0
    for it in new[:MAX_POSTS_PER_RUN]:
        if send(render(it), dry):
            seen.add(it["key"])
            if it.get("alt"):
                seen.add(it["alt"])
            st["log"].append({"at": datetime.now(KST).isoformat(), "kind": it["kind"],
                              "label": it["label"], "title": it["title"], "link": it["link"]})
            posted += 1
            time.sleep(0 if dry else 3)
    print(f"새 항목 {len(new)}건 중 {posted}건 게시" + (" (dry-run)" if dry else ""))
    st["seen"], st["nkeys"] = sorted(seen), sorted(nkeys)
    if not dry and since_days is None:
        save_state(st)


def run_weekly(dry):
    st = load_state() or {"log": []}
    now = datetime.now(KST)
    start = now - timedelta(days=7)
    week = [x for x in st["log"] if x["at"] >= start.isoformat()]
    if not week:
        print("이번 주 게시물 없음: 주간 정리 생략")
        return
    e = html.escape
    order = [("show", "방송"), ("news", "기사"), ("video", "영상"), ("home", "망원정x")]
    lines = [f"🗓 <b>이번 주 모험담</b> ({start.month}.{start.day}~{now.month}.{now.day})", ""]
    for kind, name in order:
        xs = [x for x in week if x["kind"] == kind]
        if not xs:
            continue
        lines.append(f"{ICON[kind]} <b>{name} {len(xs)}건</b>")
        for x in xs:
            lines.append(f"· <a href=\"{e(x['link'], quote=True)}\">{e(x['title'][:60], quote=False)}</a>")
        lines.append("")
    lines.append("거대한 전투보다 오늘 지킨 작은 일들. 다음 주에도 계속됩니다.")
    text = "\n".join(lines)
    if len(text) > 4000:  # 텔레그램 메시지 한도
        text = text[:3990] + "…"
    send(text, dry)


INTRO = """🗡 <b>장혜영의 하찮은 모험담</b>

장혜영이 어디서 무슨 말을 했는지, 올라오는 대로 모아 전해요.

📡 <b>이런 곳에서 가져와요</b>
망원정x 홈페이지 · 장혜영 유튜브 · 출연 방송(JTBC 장르만 여의도, CBS 박성태의 뉴스쇼, cpbc 김준일의 시사천국, BBS 아침저널) · '장혜영' 언론 보도

🗓 매주 일요일엔 한 주를 묶은 <b>'이번 주 모험담'</b>을 올려요.

거대한 전투 대신 아침 라디오 한 꼭지, 칼럼 한 편, 동네 모임 한 번.
그런 일들이 쌓여 세상은 분명히 변하고 있다고 믿으며, 빠짐없이 기록합니다.

"모두가 무사히 할머니, 할아버지가 될 수 있는 사회"를 향해.
지는 것에 익숙해지지 맙시다.

※ 지지자가 운영하는 비공식 채널입니다."""


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    since = int(args[args.index("--since") + 1]) if "--since" in args else None
    if "intro" in args:  # 고정 소개글 1회 게시 (Actions 수동 실행 mode=intro)
        send(INTRO, dry)
    elif "weekly" in args:
        run_weekly(dry)
    else:
        run_update(dry, since)
