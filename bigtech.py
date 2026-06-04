import feedparser
from google import genai
import os
import json
import email.utils
import urllib.parse
import urllib.request
import time
from datetime import datetime
import googlenewsdecoder
import trafilatura
from bs4 import BeautifulSoup
from newspaper import Article
import cloudscraper
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_KEY = os.environ.get("GEMINI_API_KEY") 
client = genai.Client(api_key=API_KEY)

MAX_ITEMS_PER_FILE = 100
MAX_TOTAL_ITEMS = 300

PAYWALL_KEYWORDS = [
    "subscribe to read", "log in to continue", "please subscribe", 
    "for subscribers only", "to continue reading this article", 
    "to read the full story, subscribe", "구독하여 전체 기사 읽기", 
    "유료 구독자 전용", "로그인 후 계속 읽기", "구독하여 기사 전체 읽기",
    "read the rest of this story", "this article is exclusive",
    "start your free trial", "already a subscriber?",
    "유료 회원 전용", "프리미엄 구독", "이 기사는 유료", "멤버십 가입", "무료 회원가입하고"
]

def log_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            # 윈도우 한글 콘솔 인코딩 에러 발생 시 크래시를 전격 예방하고 대체 문자로 출력
            print(str(msg).encode('ascii', errors='replace').decode('ascii'))
        except:
            pass
    # Local file logging removed as console output is captured by Actions

def extract_date_from_detail(html):
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 기사 헤더 메타 텍스트에서 직접 날짜 패턴 탐색 (일반 p/span 태그 텍스트 우선 매칭)
        import re
        date_regex = re.compile(
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,\s*\d{4}\b'
            r'|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b',
            re.IGNORECASE
        )
        for tag in soup.find_all(class_=lambda c: c and any(x in c.lower() for x in ['meta', 'date', 'header', 'publish'])):
            txt = tag.get_text(separator=' ', strip=True)
            match = date_regex.search(txt)
            if match:
                return match.group(0)
        
        # 2. Look for meta tags
        meta_keys = [
            "article:published_time", "article:modified_time", "og:updated_time",
            "publish-date", "published_time", "dc.date", "date", "parsely-pub-date"
        ]
        for key in meta_keys:
            meta = soup.find('meta', attrs={"property": key}) or soup.find('meta', attrs={"name": key}) or soup.find('meta', attrs={"itemprop": key})
            if meta and meta.get('content'):
                return meta.get('content').strip()
        
        # 3. Look for ld+json
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                ld = json.loads(script.string)
                if isinstance(ld, dict):
                    date = ld.get('datePublished') or ld.get('dateModified')
                    if date: return date
                elif isinstance(ld, list):
                    for item in ld:
                        date = item.get('datePublished') or item.get('dateModified')
                        if date: return date
            except:
                pass
                
        # 4. Look for time tag
        for time_tag in soup.find_all('time'):
            if time_tag.get('datetime'):
                return time_tag.get('datetime')
            text = time_tag.get_text(strip=True)
            if text and any(char.isdigit() for char in text):
                return text
    except Exception:
        pass
    return ""

def is_valid_title_similarity(original_title, extracted_title):
    if not original_title or not extracted_title:
        return False
    import re
    def get_keywords(text):
        # Remove publisher suffixes (e.g., " - 연합뉴스", " | YTN")
        core = text
        for sep in [' - ', ' | ', ' : ']:
            if sep in text:
                core = text.split(sep)[0]
                break
        words = re.findall(r'[a-zA-Z0-9가-힣]+', core.lower())
        return {w for w in words if len(w) >= 2}

    orig_keywords = get_keywords(original_title)
    new_keywords = get_keywords(extracted_title)
    if not orig_keywords or not new_keywords:
        return True  # Lenient if keywords are empty
    overlap = orig_keywords.intersection(new_keywords)
    return len(overlap) > 0

def extract_title_from_detail(html_content):
    if not html_content:
        return ""
    import html
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 사이트명 가져오기 (og:site_name) - 메타 제목이 사이트명과 완전히 일치해 제목이 오염되는 것을 방지
        site_name = ""
        site_meta = soup.find('meta', attrs={"property": "og:site_name"}) or soup.find('meta', attrs={"name": "og:site_name"})
        if site_meta and site_meta.get('content'):
            site_name = site_meta.get('content').strip().lower()
            
        # 1. JSON-LD 헤드라인/이름/제목 찾기
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                ld = json.loads(script.string)
                if isinstance(ld, dict):
                    ld_type = ld.get('@type', '')
                    if isinstance(ld_type, str) and ld_type.lower() in ['organization', 'website']:
                        continue
                    title = ld.get('headline') or ld.get('name') or ld.get('title')
                    if title:
                        val = html.unescape(title.strip())
                        if not site_name or val.lower() != site_name:
                            return val
                elif isinstance(ld, list):
                    for item in ld:
                        ld_type = item.get('@type', '')
                        if isinstance(ld_type, str) and ld_type.lower() in ['organization', 'website']:
                            continue
                        title = item.get('headline') or item.get('name') or item.get('title')
                        if title:
                            val = html.unescape(title.strip())
                            if not site_name or val.lower() != site_name:
                                return val
            except:
                pass
                
        # 2. Meta Tags (Open Graph 등)
        meta_keys = [
            "og:title", "twitter:title", "title", "headline", "parsely-title"
        ]
        for key in meta_keys:
            meta = soup.find('meta', attrs={"property": key}) or soup.find('meta', attrs={"name": key}) or soup.find('meta', attrs={"itemprop": key})
            if meta and meta.get('content'):
                val = html.unescape(meta.get('content').strip())
                # 추출된 제목이 사이트명과 동일하면 기사 제목이 아니므로 무시하고 다음으로 넘어감
                if site_name and val.lower() == site_name:
                    continue
                return val
                
        # 3. H1 태그 찾기 (상세 기사 페이지의 본문 제목은 일반적으로 h1에 위치함)
        h1 = soup.find('h1')
        if h1:
            h1_text = h1.get_text(strip=True)
            if h1_text and (not site_name or h1_text.lower() != site_name) and len(h1_text) > 3:
                return h1_text
                
        # 4. HTML 기본 <title> 태그 찾기
        if soup.title and soup.title.string:
            val = html.unescape(soup.title.string.strip())
            return val
    except Exception:
        pass
    return ""

def parse_date_to_ms(d_str):
    if not d_str:
        return None
    d_str = d_str.strip()
    try:
        dt = email.utils.parsedate_to_datetime(d_str)
        return int(dt.timestamp() * 1000)
    except:
        try:
            clean_date = d_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(clean_date)
            return int(dt.timestamp() * 1000)
        except:
            try:
                dt = datetime.strptime(d_str, "%b %d, %Y")
                return int(dt.timestamp() * 1000)
            except:
                try:
                    dt = datetime.strptime(d_str, "%B %d, %Y")
                    return int(dt.timestamp() * 1000)
                except:
                    try:
                        dt = datetime.strptime(d_str[:10], "%Y-%m-%d")
                        return int(dt.timestamp() * 1000)
                    except:
                        pass
    return None

log_print(f"\n{'='*50}\n[BigTech] 공식 블로그 직접 수집 시작\n{'='*50}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIGTECH_DIR = os.path.join(BASE_DIR, "bigtech")
os.makedirs(BIGTECH_DIR, exist_ok=True)

# 1. 기존 BigTech JSON 데이터 불러오기 (중복 방지용)
bigtech_existing_articles = []
for i in range(1, 4):
    file_path = os.path.join(BIGTECH_DIR, f"p{i}.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                bigtech_existing_articles.extend(data.get("articles", []))
            except Exception:
                pass

def normalize_link(url):
    if not url: return ""
    return str(url).strip().rstrip('/')

existing_bigtech_links = {normalize_link(a.get("link")) for a in bigtech_existing_articles}
scraped_candidates = []
current_time = time.time()

def add_bigtech_candidate(company, title, link, pub_date="", timestamp_ms=None, source=""):
    # Google 기사인 경우 특정 URL 경로가 포함된 기사만 수집
    if company == "Google":
        required_substrings = [
            "innovation-and-ai/models-and-research",
            "innovation-and-ai/technology/ai",
            "innovation-and-ai/technology/research"
        ]
        if not any(sub in link for sub in required_substrings):
            return

    # 2. 가져온 리스트 중에 bigtech 폴더에 있는 기사이면 리스트에서 제거
    normalized_url = normalize_link(link)
    if normalized_url not in existing_bigtech_links:
        # 3. 기사 발행일이 7일이 지났으면 리스트에서 제거 (오래된 과거 기사는 후보군에 들어오는 것을 원천 사전 차단)
        # 단, 글 작성 주기가 상대적으로 느린 Microsoft와 NVIDIA는 30일 범위까지 예외 허용
        if timestamp_ms is not None:
            age_days = (current_time - (timestamp_ms / 1000)) / (24 * 3600)
            allowed_days = 60 if company in ["Microsoft", "NVIDIA", "Meta", "xAI"] else 7
            if age_days > allowed_days:
                return
                
        scraped_candidates.append({
            "company": company,
            "title": title.strip(),
            "link": normalized_url,
            "published": pub_date,
            "timestamp_ms": timestamp_ms,
            "source": source
        })

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def get_date_from_parent(a_tag):
    time_tag = a_tag.find('time')
    if time_tag:
        return time_tag.get('datetime') or time_tag.get_text(strip=True)
    date_span = a_tag.find('span', class_=lambda c: c and 'date' in c.lower())
    if date_span:
        return date_span.get_text(strip=True)

    parent = a_tag.find_parent(['li', 'article'])
    if not parent:
        parent = a_tag.find_parent('div', class_=lambda c: c and any(x in c.lower() for x in ['card', 'item', 'post']))
    if not parent:
        parent = a_tag.find_parent('div')
    if parent:
        time_tag = parent.find('time')
        if time_tag:
            return time_tag.get('datetime') or time_tag.get_text(strip=True)
        date_span = parent.find('span', class_=lambda c: c and 'date' in c.lower())
        if date_span:
            return date_span.get_text(strip=True)
    return ""

# 1. Google 수집 (RSS 기반 - 기존 비활성화 상태 보존)
def collect_google():
    try:
        log_print("[Google] 수집 시작...")
        google_feeds = [
            "https://blog.google/innovation-and-ai/models-and-research/google-deepmind/rss/",
            "https://blog.google/innovation-and-ai/models-and-research/google-research/rss/",
            "https://blog.google/innovation-and-ai/models-and-research/google-labs/rss/",
            "https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/",
            "https://blog.google/innovation-and-ai/models-and-research/quantum-computing/rss/"
        ]
        for feed_url in google_feeds:
            feed = feedparser.parse(feed_url)
            for e in feed.entries[:15]: 
                pub_date = e.get("published", "")
                timestamp_ms = parse_date_to_ms(pub_date)
                add_bigtech_candidate("Google", e.title, e.link, pub_date, timestamp_ms, source="rss")
    except Exception as e: log_print(f"Google 수집 에러: {e}")

# 2. Anthropic 수집 (News, Blog, Research - HTML + Sitemap Hybrid - 기존 비활성화 상태 보존)
def collect_anthropic():
    try:
        log_print("[Anthropic] 수집 시작...")
        # 파트 1. Sitemap 파싱 수집 (누락 완벽 방지)
        try:
            sitemap_res = requests.get("https://www.anthropic.com/sitemap.xml", timeout=15, verify=False)
            sitemap_soup = BeautifulSoup(sitemap_res.text, 'html.parser')
            # <url> 태그 단위로 순회하여 loc와 lastmod를 정확히 매칭시킵니다.
            for url_tag in sitemap_soup.find_all('url'):
                loc = url_tag.find('loc')
                lastmod = url_tag.find('lastmod')
                if loc:
                    url_str = loc.get_text(strip=True)
                    if '/news/' in url_str or '/blog/' in url_str or '/research/' in url_str:
                        if any(x in url_str for x in ['/research/team/', '/team/', '/author/', '/team-', '/tags/', '/categories/', '/category/']):
                            continue
                        # 슬래시 정제
                        link = normalize_link(url_str)
                        
                        # lastmod의 날짜(발행/수정일)를 가로채어 타임스탬프로 환산
                        pub_date = lastmod.get_text(strip=True) if lastmod else ""
                        timestamp_ms = parse_date_to_ms(pub_date) if pub_date else None
                        
                        # 수집 적재 단계에 날짜 메타를 제공함으로써 최근 7일이 지난 기사는 사전 차단(리소스를 극적으로 아낌)
                        add_bigtech_candidate("Anthropic", "Anthropic Article", link, pub_date, timestamp_ms, source="sitemap")
        except Exception as sitemap_err:
            log_print(f"Anthropic Sitemap 수집 에러 (HTML Fallback 작동): {sitemap_err}")

        # 파트 2. 기존 HTML 스크래핑 수집
        anthropic_urls = ["https://www.anthropic.com/news", "https://claude.com/blog", "https://www.anthropic.com/research"]
        for url in anthropic_urls:
            res = scraper.get(url, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                if '/news/' in href or '/blog/' in href or '/research/' in href:
                    if any(x in href for x in ['/research/team/', '/team/', '/author/', '/tags/', '/categories/', '/category/']):
                        continue
                    
                    heading = a.find(['h1', 'h2', 'h3', 'h4', 'h5'])
                    title = heading.get_text(strip=True) if heading else a.get_text(separator=' ', strip=True)
                    
                    if len(title) > 15 and "read more" not in title.lower():
                        base = "https://www.anthropic.com" if "anthropic.com" in url else "https://claude.com"
                        link = base + href if href.startswith('/') else href
                        pub_date = get_date_from_parent(a)
                        timestamp_ms = parse_date_to_ms(pub_date)
                        add_bigtech_candidate("Anthropic", title, link, pub_date, timestamp_ms, source="html")
    except Exception as e: log_print(f"Anthropic 수집 에러: {e}")

# 3. OpenAI 수집 (공식 뉴스 RSS 피드를 통한 수집 - 활성화 상태 보존)
def collect_openai():
    try:
        log_print("[OpenAI] 수집 시작...")
        # 파트 1. 공식 뉴스 RSS 수집 (requests와 verify=False로 확실히 가져와 파싱)
        openai_feed = "https://openai.com/news/rss.xml"
        rss_res = requests.get(openai_feed, timeout=15, verify=False)
        feed = feedparser.parse(rss_res.text)
        for e in feed.entries[:45]:
            pub_date = e.get("published", "")
            timestamp_ms = parse_date_to_ms(pub_date)
            add_bigtech_candidate("OpenAI", e.title, e.link, pub_date, timestamp_ms, source="rss")


        # 파트 2. 공식 리서치 사이트맵 수집 (리서치 논문/아티클 누락 차단 - 이전의 과다 수집 방지를 위해 구버전과 동일하게 비활성화)
        # try:
        #     openai_sitemaps = [
        #         "https://openai.com/sitemap.xml/research/"
        #     ]
        #     for openai_sitemap in openai_sitemaps:
        #         try:
        #             sitemap_res = requests.get(openai_sitemap, timeout=15, verify=False)
        #             sitemap_soup = BeautifulSoup(sitemap_res.text, 'html.parser')
        #             # <url> 태그 단위로 순회하여 loc와 lastmod를 정확히 매칭시킵니다.
        #             for url_tag in sitemap_soup.find_all('url'):
        #                 loc = url_tag.find('loc')
        #                 lastmod = url_tag.find('lastmod')
        #                 if loc:
        #                     url_str = loc.get_text(strip=True)
        #                     # 호스트 뒤에 정규 경로가 바로 오는 영어/기본 기사만 필터링 (다국어 페이지 차단)
        #                     if url_str.startswith("https://openai.com/index/") or url_str.startswith("https://openai.com/news/") or url_str.startswith("https://openai.com/research/"):
        #                         # 슬래시 정제
        #                         link = normalize_link(url_str)
        #                         # 단순 목록/메인 페이지 자체는 제외
        #                         if link in ["https://openai.com/news", "https://openai.com/research", "https://openai.com/index"]:
        #                             continue
        #                         
        #                         # lastmod의 날짜를 가로채어 7일이 지난 기사는 사전 차단
        #                         pub_date = lastmod.get_text(strip=True) if lastmod else ""
        #                         timestamp_ms = parse_date_to_ms(pub_date) if pub_date else None
        #                         
        #                         # 제목은 루프 돌며 상세페이지 방문 시 100% 자동 복원되므로 임시 지정하여 후보군에 안착
        #                         add_bigtech_candidate("OpenAI", "OpenAI Research Article", link, pub_date, timestamp_ms, source="sitemap")
        #         except Exception as sitemap_err:
        #             log_print(f"OpenAI 사이트맵 수집 에러 ({openai_sitemap}): {sitemap_err}")
        # except Exception as e: log_print(f"OpenAI 수집 에러: {e}")
    except Exception as e:
        log_print(f"OpenAI 수집 에러: {e}")


# 4. NVIDIA 수집 (공식 개발자 블로그 RSS 피드 수집 - 활성화)
def collect_nvidia():
    try:
        log_print("[NVIDIA] 수집 시작...")
        nvidia_feed = "https://developer.nvidia.com/blog/feed/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        res = requests.get(nvidia_feed, headers=headers, timeout=15, verify=False)
        feed = feedparser.parse(res.text)
        for e in feed.entries[:15]: 
            pub_date = e.get("published", "")
            timestamp_ms = parse_date_to_ms(pub_date)
            add_bigtech_candidate("NVIDIA", e.title, e.link, pub_date, timestamp_ms, source="rss")
    except Exception as e: log_print(f"NVIDIA 수집 에러: {e}")

# 5. Microsoft 수집 (공식 AI Tag 및 Research RSS 피드 통합 루프 수집 - 양자 컴퓨터 키워드 추가)
def collect_microsoft():
    try:
        log_print("[Microsoft] 수집 시작 (공식 AI Tag & Research 피드 통합)...")
        ms_feeds = [
            {"url": "https://news.microsoft.com/source/tag/ai/feed/", "type": "ai_tag"},
            {"url": "https://www.microsoft.com/en-us/research/feed/", "type": "research"}
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        
        # AI 및 양자 관련 핵심 검출 키워드 목록
        target_keywords = ['ai', 'agent', 'machine learning', 'llm', 'intelligence', 'model', 'neural', 'quantum', 'qubit']
        
        for feed_info in ms_feeds:
            log_print(f"   -> 피드 요청 중: {feed_info['url']}")
            res = requests.get(feed_info["url"], headers=headers, timeout=15, verify=False)
            feed = feedparser.parse(res.text)
            
            for e in feed.entries[:20]:
                pub_date = e.get("published", "") or e.get("pubDate", "")
                timestamp_ms = parse_date_to_ms(pub_date)
                
                # 마이크로소프트 패밀리 도메인 확인
                allowed_domains = ["microsoft.com", "microsoft.ai", "bing.com", "windows.com"]
                if any(domain in e.link for domain in allowed_domains):
                    
                    # 리서치 피드의 경우 인공지능 및 양자 관련 키워드 선별 필터링 적용
                    if feed_info["type"] == "research":
                        title_lower = e.title.lower()
                        # 카테고리 텍스트 추출 검사
                        categories = [c.get("term", "").lower() for c in e.get("tags", []) if c.get("term")]
                        category_text = " ".join(categories)
                        
                        match_keyword = False
                        for kw in target_keywords:
                            if kw in title_lower or kw in category_text:
                                match_keyword = True
                                break
                        
                        if not match_keyword:
                            # AI/양자 관련 없는 리서치 기사는 스킵
                            continue
                            
                    add_bigtech_candidate("Microsoft", e.title, e.link, pub_date, timestamp_ms, source="rss")
    except Exception as e: log_print(f"Microsoft 수집 에러: {e}")

# 6. Meta 수집 (공식 AI 블로그 직접 HTML 스크래핑 수집 - 활성화)
def collect_meta():
    try:
        log_print("[Meta] 수집 시작...")
        url = "https://ai.meta.com/blog/"
        headers = {
            "User-Agent": "curl/8.19.0",
            "Accept": "*/*"
        }
        res = requests.get(url, headers=headers, timeout=15, verify=False)
        if res.status_code != 200:
            log_print(f"Meta 수집 실패: HTTP status {res.status_code}")
            return
            
        soup = BeautifulSoup(res.text, 'html.parser')
        a_tags = soup.find_all('a', href=True)
        
        articles_map = {}
        import re
        date_regex = re.compile(
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,\s*\d{4}\b'
            r'|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b',
            re.IGNORECASE
        )
        
        for a in a_tags:
            href = a.get('href', '').strip()
            if not href:
                continue
                
            if href.startswith('/'):
                link = "https://ai.meta.com" + href
            else:
                link = href
                
            parsed_url = urllib.parse.urlparse(link)
            path = parsed_url.path.rstrip('/')
            if not path.startswith('/blog') or path == '/blog':
                continue
            if parsed_url.query:
                continue
                
            norm_link = normalize_link(link)
            title_text = a.get_text(strip=True)
            
            ignore_keywords = ["featured", "learn more", "자세히 알아보기", "더 알아보기", "더보기", "read more", "view"]
            is_ignored = any(kw in title_text.lower() for kw in ignore_keywords) or len(title_text) < 10
            
            pub_date = ""
            curr = a
            for _ in range(3):
                if not curr:
                    break
                curr_txt = curr.get_text(separator=' ', strip=True)
                match = date_regex.search(curr_txt)
                if match:
                    pub_date = match.group(0)
                    break
                curr = curr.parent

            if norm_link not in articles_map:
                articles_map[norm_link] = {
                    "titles": [],
                    "pub_date": pub_date
                }
                
            if title_text and not is_ignored:
                articles_map[norm_link]["titles"].append(title_text)
            if pub_date and not articles_map[norm_link]["pub_date"]:
                articles_map[norm_link]["pub_date"] = pub_date

        for link, info in articles_map.items():
            titles = info["titles"]
            if not titles:
                continue
            
            best_title = max(titles, key=len)
            pub_date = info["pub_date"]
            timestamp_ms = parse_date_to_ms(pub_date) if pub_date else None
            
            add_bigtech_candidate("Meta", best_title, link, pub_date, timestamp_ms, source="html")
            
    except Exception as e:
        log_print(f"Meta 수집 에러: {e}")

# 7. xAI 수집 (공식 뉴스 페이지 직접 HTML 스크래핑 수집 - 활성화)
def collect_xai():
    try:
        log_print("[xAI] 수집 시작...")
        url = "https://x.ai/news"
        res = requests.get(url, timeout=15)
        if res.status_code != 200:
            log_print(f"xAI 수집 실패: HTTP status {res.status_code}")
            return
            
        soup = BeautifulSoup(res.text, 'html.parser')
        # 최신 메인 포스트(h1/h2) 및 일반 목록 포스트(h3) 전수 수집
        heading_elements = soup.find_all(['h1', 'h2', 'h3'])
        
        # 날짜 추출용 정규식
        import re
        date_regex = re.compile(
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+20\d{2}\b',
            re.IGNORECASE
        )
        
        for heading in heading_elements:
            title = heading.get_text(strip=True)
            if not title:
                continue
                
            # 헤더의 부모 앵커 태그를 찾아 기사 링크 획득
            parent_a = heading.find_parent('a', href=True)
            if not parent_a:
                continue
                
            href = parent_a.get('href', '').strip()
            if not href:
                continue
                
            # 절대 경로 변환
            if href.startswith('/'):
                link = "https://x.ai" + href
            else:
                link = href
                
            norm_link = normalize_link(link)
            
            # 카드 박스(div, li, article) 텍스트에서 날짜 검색
            pub_date = ""
            box = heading.find_parent(['div', 'li', 'article'])
            if box:
                box_text = box.get_text(separator=' ', strip=True)
                match = date_regex.search(box_text)
                if match:
                    pub_date = match.group(0)
            
            # 박스 내에서 날짜가 안 보일 경우, 형제 노드들 텍스트 검색
            if not pub_date:
                siblings_txt = " ".join([s.get_text(strip=True) for s in heading.find_previous_siblings() + heading.find_next_siblings() if s])
                match = date_regex.search(siblings_txt)
                if match:
                    pub_date = match.group(0)
                    
            timestamp_ms = parse_date_to_ms(pub_date) if pub_date else None
            
            add_bigtech_candidate("xAI", title, norm_link, pub_date, timestamp_ms, source="html")
            
    except Exception as e:
        log_print(f"xAI 수집 에러: {e}")

# 각 빅테크 회사의 수집 동작 실행 (전체 빅테크 기업 일괄 기동)
collect_google()
collect_anthropic()
collect_openai()
collect_nvidia()
collect_microsoft()
collect_meta()
collect_xai()



# 3. 수집된 후보군 내 자체 중복 제거
unique_candidates = []
seen_cand_links = set()
for cand in scraped_candidates:
    norm_link = normalize_link(cand["link"])
    if norm_link not in seen_cand_links:
        seen_cand_links.add(norm_link)
        unique_candidates.append(cand)

log_print(f"새로 수집된 BigTech 후보 기사: {len(unique_candidates)}개 (기존 중복 제외)")

# 4. 나머지 기사들의 본문을 가져온다.
new_bigtech_articles = []
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

for idx, cand in enumerate(unique_candidates):
    log_print(f" - [{idx+1}/{len(unique_candidates)}] [{cand['company']}] {cand['title']}")
    log_print(f"   URL: {cand['link']}")
    
    # Meta 기사의 경우 차단 방지를 위해 심플 헤더 사용
    current_headers = headers.copy()
    if cand['company'] == "Meta":
        current_headers = {
            "User-Agent": "curl/8.19.0",
            "Accept": "*/*"
        }
        
    html_content = None
    try:
        response = requests.get(cand['link'], headers=current_headers, timeout=20)
        if response.status_code == 200: html_content = response.text
        else: raise RuntimeError(f"HTTP {response.status_code}")
    except:
        try:
            response = scraper.get(cand['link'], headers=current_headers, timeout=20)
            if response.status_code == 200: html_content = response.text
        except: pass
        
    if html_content:
        detail_title = extract_title_from_detail(html_content)
        if detail_title and is_valid_title_similarity(cand['title'], detail_title):
            cand['title'] = detail_title

    content = None
    if html_content:
        try:
            content = trafilatura.extract(html_content)
            if content and len(content.strip()) > 100: content = content[:4000]
            else: raise ValueError("Too short")
        except:
            try:
                article = Article(url=cand['link'])
                article.set_html(html_content)
                article.parse()
                content = article.text
                if not content or len(content.strip()) <= 100: raise ValueError("Too short")
                content = content[:4000]
            except:
                try:
                    soup = BeautifulSoup(html_content, 'html.parser')
                    for noise in soup(['script', 'style', 'nav', 'footer', 'aside', 'header']): noise.decompose()
                    main_area = soup.find('article') or soup
                    paragraphs = main_area.find_all('p')
                    content = '\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
                    if not content or len(content.strip()) <= 100: raise ValueError("Too short")
                    content = content[:4000]
                except: pass
                
    if not content:
        log_print("   -> 본문 추출 실패. 요약 생략.")
        continue
        
    log_print(f"   -> 본문 추출 성공 (길이: {len(content)}자)")
        
    pub_date = cand.get("published", "")
    timestamp_ms = cand.get("timestamp_ms")
    source = cand.get("source", "")
        
    if html_content and source != "rss":
        detail_date = extract_date_from_detail(html_content)
        if detail_date:
            detail_ts = parse_date_to_ms(detail_date)
            if detail_ts:
                pub_date = detail_date
                timestamp_ms = detail_ts

    if timestamp_ms is None:
        log_print("   -> 날짜 정보 없음. 최근 기사 여부를 판별할 수 없어 수집에서 제외합니다.")
        continue
        
    # RSS에 날짜가 없어서 본문 추출 후 확인한 경우에 대한 60일 필터링 추가 검증
    age_days = (current_time - (timestamp_ms / 1000)) / (24 * 3600)
    if age_days > 60:
        log_print(f"   -> 오래된 기사 제외 (발행일: {pub_date}, 경과일수: {age_days:.1f}일)")
        continue

    # 5. 제미나이 API를 통해 요약
    prompt = f"""당신은 세계적인 AI 산업 전문 애널리스트입니다.
아래 기사의 본문을 읽고 핵심을 관통하는 명확하고 간결한 1~2문장 요약을 한국어와 영어로 각각 작성해 주세요.
반드시 아래 JSON 형식으로만 반환해 주세요. (마크다운 기호 없이 순수 JSON만 출력)

{{
  "summary_kr": "한국어 요약 내용...",
  "summary_en": "English summary content..."
}}

[기사 제목] {cand['title']}
[기사 본문]
{content}
"""
    summary_kr, summary_en = "", ""
    last_error = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt
            )
            res_text = resp.text.strip()
            if res_text.startswith("```json"): res_text = res_text[7:-3].strip()
            elif res_text.startswith("```"): res_text = res_text[3:-3].strip()
            
            sum_data = json.loads(res_text)
            summary_kr = sum_data.get("summary_kr", "")
            summary_en = sum_data.get("summary_en", "")
            if not summary_kr or not summary_en:
                raise ValueError(f"요약 결과에서 키(summary_kr, summary_en) 누락: {res_text}")
            break
        except Exception as e:
            last_error = e
            time.sleep(5)
            
    if not summary_kr or not summary_en:
        log_print(f"   -> 제미나이 요약 생성 실패. 생략. (사유: {last_error})")
        continue
        
    # 6. 요약한 정보를 포함해서 json에 저장하기 위해 리스트에 추가
    human_readable_date = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d')
    new_bigtech_articles.append({
        "company": cand['company'],
        "timestamp": timestamp_ms,
        "date": human_readable_date,
        "title": cand['title'],
        "summary_kr": summary_kr,
        "summary_en": summary_en,
        "link": cand['link']
    })
    log_print(f"   -> 완료: {summary_kr[:50]}...")
    log_print(f"   -> 완료 ({human_readable_date}): {summary_kr[:30]}...")
    time.sleep(2)

# 5. bigtech JSON 파일 병합 및 저장
if new_bigtech_articles:
    # 새로 수집된 기사들을 최신순(timestamp 내림차순) 정렬하여 p1 최상단에 안정적으로 안착
    new_bigtech_articles.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    bigtech_all = new_bigtech_articles + bigtech_existing_articles
    bigtech_all = bigtech_all[:MAX_TOTAL_ITEMS]
    
    last_update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i in range(3):
        start_idx = i * MAX_ITEMS_PER_FILE
        end_idx = start_idx + MAX_ITEMS_PER_FILE
        chunk = bigtech_all[start_idx:end_idx]
        
        file_path = os.path.join(BIGTECH_DIR, f"p{i+1}.json")
        chunk_data = {
            "last_update": last_update_time,
            "articles": chunk
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=4)
            
    log_print(f"\n[BigTech] {len(new_bigtech_articles)}개의 새 기사 업데이트 완료!")
else:
    log_print("\n[BigTech] 새로 업데이트할 기사가 없습니다.")
