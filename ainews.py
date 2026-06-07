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
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import builtins
def safe_print(*args, **kwargs):
    import sys
    try:
        builtins.print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        new_args = []
        for arg in args:
            if isinstance(arg, str):
                new_args.append(arg.encode(encoding, errors='replace').decode(encoding))
            else:
                new_args.append(arg)
        try:
            builtins.print(*new_args, **kwargs)
        except Exception:
            pass

print = safe_print

def normalize_link(url):
    if not url: return ""
    return str(url).strip().rstrip('/')

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

def get_publisher_suffix(original_title):
    if not original_title:
        return ""
    for sep in [' - ', ' | ', ' : ']:
        if sep in original_title:
            parts = original_title.split(sep)
            if len(parts) > 1:
                return sep + parts[-1].strip()
    return ""

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
                    if isinstance(ld_type, str) and ld_type.lower() in ['organization', 'website', 'person']:
                        continue
                    title = ld.get('headline') or ld.get('name') or ld.get('title')
                    if title:
                        val = html.unescape(title.strip())
                        if not site_name or val.lower() != site_name:
                            return val
                elif isinstance(ld, list):
                    for item in ld:
                        ld_type = item.get('@type', '')
                        if isinstance(ld_type, str) and ld_type.lower() in ['organization', 'website', 'person']:
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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_KEY = os.environ.get("GEMINI_API_KEY") 
client = genai.Client(api_key=API_KEY)

MAX_ITEMS_PER_FILE = 100
MAX_TOTAL_ITEMS = 300

# 1-1. 한국 유료 결제 장벽(Paywall) 매체 도메인
KOREAN_PAYWALL_DOMAINS = [
    "premium.naver.com", "themiilk.com", "outstanding.kr"
]

# 1-2. 해외 유료 결제 장벽(Paywall) 매체 도메인
GLOBAL_PAYWALL_DOMAINS = [
    "wsj.com", "bloomberg.com", "barrons.com", "nytimes.com", "ft.com", 
    "thetimes.co.uk", "economist.com", "seekingalpha.com", "theinformation.com", 
    "fortune.com"
]

# 2-1. 한국 비뉴스성 도메인 (블로그, SNS, 보도자료 등 - 필요 시 추가)
KOREAN_SOCIAL_GOSSIP_DOMAINS = []

# 2-2. 해외 비뉴스성 도메인 (개인 블로그, SNS, 가십, 보도자료 등)
GLOBAL_SOCIAL_GOSSIP_DOMAINS = [
    "medium.com", "facebook.com", "twitter.com", "youtube.com",
    "prnewswire.com", "businesswire.com", "globenewswire.com",
    "inshorts.com", "technologyreview.com", "forbes.com", "nbcnews.com"
]

# 3. 최종 제외 대상 도메인 통합 리스트 (기존 필터 로직 호환용)
EXCLUDE_DOMAINS = (
    KOREAN_PAYWALL_DOMAINS + 
    GLOBAL_PAYWALL_DOMAINS + 
    KOREAN_SOCIAL_GOSSIP_DOMAINS + 
    GLOBAL_SOCIAL_GOSSIP_DOMAINS
)

PAYWALL_KEYWORDS = [
    "subscribe to read", "log in to continue", "please subscribe", 
    "for subscribers only", "to continue reading this article", 
    "to read the full story, subscribe", "구독하여 전체 기사 읽기", 
    "유료 구독자 전용", "로그인 후 계속 읽기", "구독하여 기사 전체 읽기",
    "read the rest of this story", "this article is exclusive",
    "start your free trial", "already a subscriber?",
    "유료 회원 전용", "프리미엄 구독", "이 기사는 유료", "멤버십 가입", "무료 회원가입하고"
]

CONFIGS = [
    {
        "lang_name": "Korean",
        "max_rss_items": 30,
        "query": "AI AND (오픈AI OR chatGPT OR 챗GPT OR 구글 OR 딥마인드 OR 메타 OR 앤트로픽 OR 클로드 OR 엔비디아 OR 마이크로소프트 OR 애플 OR 아마존) AND (연구 OR 개발 OR 트렌드 OR 전략 OR 출시 OR 규제 OR 혁신) -주식 -테마주 -증시 -특징주 when:12h",
        "rss_params": "hl=ko&gl=KR&ceid=KR:ko",
        "output_dir": "ai_news_kr",
        "prompt1": """당신은 세계적인 AI 산업 전문 애널리스트이자 수석 뉴스 편집장입니다.
다음은 오늘 수집된 {count}개의 AI 뉴스 기사 제목입니다. 
이 중에서 산업 동향에 가장 큰 영향을 미칠 '핵심 뉴스'를 기본적으로 최대 3개 엄선하되, 정말 중요한 뉴스가 많다고 판단될 경우 예외적으로 최대 5개까지 선정해 주세요.

[엄선 기준]
1. 당일 중복 배제 (매우 중요): 같은 기업의 동일한 사건, 제품 출시, 정책 변경 등을 다루는 기사들은 의미가 겹치므로 반드시 가장 포괄적인 기사 딱 1개만 선택하세요.
2. 과거 기사와 중복 배제: 다음은 최근에 이미 수집된 기사 제목들입니다. 이 목록에 있는 주제와 겹치는 기사(타 매체에서 뒤늦게 보도한 동일 주제 기사 등)는 절대 선택하지 마세요.
[최근 수집된 기사]
{recent_titles_text}

3. 중요도 우선: 단순 가십성, 단순 주가 등락, 기업 홍보성 기사는 철저히 제외하세요. 대신 '기술적 돌파구(혁신)', '대규모 투자 및 M&A', '주요 국가의 규제/정책 변화', '기업의 실제 AI 도입 및 성과'를 다룬 기사를 최우선으로 선택하세요.
4. 수량 유연성: 기본 최대 3개(중요 뉴스가 많으면 최대 5개)입니다. 전체 기사 중 정말 중요하다고 판단되는 뉴스가 없다면 억지로 채우지 말고 0~2개만 선정해도 좋습니다.
5. 유료 기사 배제: 블룸버그, 월스트리트저널, 뉴욕타임스, 파이낸셜타임스 등 전문을 읽기 위해 유료 구독이나 로그인이 필요한 매체의 기사는 선택하지 마세요.

결과는 반드시 아래 JSON 형식으로만 반환해 주세요. 마크다운 기호(```json 등)는 제외하고 순수 JSON 텍스트만 출력해 주세요.
선택된 기사의 id 목록과 함께, 오늘 기사들의 전반적인 경향 및 각 기사들의 선정/배제 사유를 요약한 종합 편집자 브리핑(editorial_note)을 한국어로 포함해 주세요.

{{
  "selected_ids": [0, 3],
  "editorial_note": "오늘 수집된 기사 중... 이러한 이유로 0번과 3번을 최종 선정하고, 나머지 기사들은 이러이러한 이유로 배제했습니다."
}}

[뉴스 리스트]
{title_only_text}
""",
        "prompt2": """당신은 세계적인 AI 산업 전문 애널리스트이자 수석 뉴스 편집장입니다.
다음은 엄선된 {count}개의 AI 뉴스 기사 제목과 본문 내용 일부입니다. 

[요약 가이드라인]
1. 길이 및 형식: 각 기사당 핵심을 관통하는 명확하고 간결한 1~2문장으로 요약하세요. (접속사를 남발하는 복잡한 만연체는 피하세요.)
2. 어조: '~했습니다', '~전망입니다' 등 정중하고 객관적인 뉴스 보도 어조를 사용하세요.
3. 내용: 단순히 기사 제목을 다르게 표현하는 데 그치지 말고, 반드시 [본문 일부]를 꼼꼼히 읽고 그 안의 '구체적인 사실, 수치, 배경, 핵심 이유' 등을 포함하여 깊이 있게 작성하세요.

결과는 반드시 아래 JSON 배열 형식으로만 반환해 주세요. 마크다운 기호(```json 등)는 제외하고 순수 JSON 텍스트만 출력해 주세요.

[
  {{"id": 0, "summary_kr": "오픈AI가 새로운 에이전트 AI 모델을 출시하며 글로벌 기업 시장 공략에 본격적으로 나섰습니다."}},
  {{"id": 3, "summary_kr": "엔비디아의 차세대 AI 칩 양산이 지연됨에 따라, 관련 서버 및 클라우드 업계의 4분기 실적에 타격이 예상됩니다."}}
]

[뉴스 리스트]
{selected_news_text}
"""
    },
    {
        "lang_name": "English",
        "max_rss_items": 40,
        "query": "AI AND (OpenAI OR ChatGPT OR Google OR DeepMind OR Meta OR Microsoft OR Nvidia OR Anthropic OR Claude OR Apple OR Amazon OR AWS) AND (Research OR Development OR Trend OR Strategy OR Release OR Regulation OR Innovation) -investing -\"stock price\" -\"market watch\" -\"analyst rating\" when:12h",
        "rss_params": "hl=en-US&gl=US&ceid=US:en",
        "output_dir": "ai_news",
        "prompt1": """You are a world-class AI industry analyst and chief news editor.
Here are the titles of {count} AI news articles collected today. 
Please select a maximum of 3 of the most impactful and important core news articles from these by default, but if there are many highly important news items, you may select up to 5.

[Selection Criteria]
1. Exclude duplicates (Very Important): Articles covering the same event, product launch, or policy change from the same company overlap in meaning, so be sure to select only the single most comprehensive article.
2. Exclude past duplicates: Here are the titles of recently collected articles. Do NOT select any article that covers the same topic (e.g., a delayed report from another publisher) as any of these.
[Recently Collected Articles]
{recent_titles_text}

3. Quality first: Strictly exclude simple gossip, minor stock movements, or promotional articles. Prioritize articles covering 'technological breakthroughs', 'major investments/M&A', 'significant regulations/policies', or 'real-world enterprise AI adoption'.
4. Quantity flexibility: The default maximum is 3 (up to 5 if highly important). If there is no truly important news, do not force yourself to fill the quota; it is okay to select 0 to 2.
5. Exclude Paywalled Articles: Do not select articles from sources that typically require a paid subscription to read the full text (e.g., Bloomberg, Wall Street Journal, Barron's, NYT, Financial Times).

The result must be returned only in the JSON format below. Exclude markdown symbols (like ```json) and output only pure JSON text.
Along with the list of selected article IDs, please include an editorial note (editorial_note) in Korean summarizing the overall trend of today's articles and the reasons for selecting/excluding them.

{{
  "selected_ids": [0, 3],
  "editorial_note": "오늘 수집된 영문 기사 중... 이러한 이유로 0번과 3번을 최종 선정하고, 나머지 기사들은 이러이러한 이유로 배제했습니다."
}}

[News List]
{title_only_text}
""",
        "prompt2": """You are a world-class AI industry analyst and chief news editor.
Here are the titles and partial body contents of {count} carefully selected AI news articles. 

[Summary Guidelines]
1. Length & Format: Provide a deep, insightful summary in 1 or 2 concise, easy-to-understand sentences per article. Avoid overly long run-on sentences.
2. Content: Do not just rephrase the title. Read the [Body Text] carefully and include 'specific facts, figures, background, or the core reason' found within it.
3. Tone: Use an objective, professional journalistic tone.

The result must be returned only in the JSON array format below. Exclude markdown symbols (like ```json) and output only pure JSON text.

[
  {{"id": 0, "summary_en": "OpenAI has officially launched a new $4 billion entity focused on the deployment of artificial intelligence technologies in the corporate sector."}},
  {{"id": 4, "summary_en": "Microsoft is reportedly considering delaying its clean energy goals due to the massive electricity demands required to power its AI operations."}}
]

[News List]
{selected_news_text}
"""
    }
]

for config in CONFIGS:
    print(f"\n{'='*50}\n[{config['lang_name']}] 뉴스 수집 시작\n{'='*50}")
    query = config["query"]
    encoded_query = urllib.parse.quote(query)
    
    RSS_URL = f"https://news.google.com/rss/search?q={encoded_query}&{config['rss_params']}"
    feed = feedparser.parse(RSS_URL, agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    max_items = config.get("max_rss_items", 40)

    OUTPUT_DIR = config["output_dir"]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    all_articles = []
    for i in range(1, 4):
        file_path = os.path.join(OUTPUT_DIR, f"p{i}.json")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                all_articles.extend(data.get("articles", []))
    
    existing_links = {normalize_link(article.get("link")) for article in all_articles if article.get("link")}
    
    recent_titles = [article.get("title") for article in all_articles[:10]]
    recent_titles_text = "\n".join([f"- {title}" for title in recent_titles]) if recent_titles else "없음 (None)"

    new_articles = []
    
    candidate_entries = []
    print(f"\n[구글 뉴스 수집 및 필터링 진행 ({len(feed.entries[:max_items])}개)]")
    for i, entry in enumerate(feed.entries[:max_items]):
        if "news.google.com" in entry.link:
            try:
                if hasattr(googlenewsdecoder, 'new_decoderv1'):
                    res = googlenewsdecoder.new_decoderv1(entry.link)
                    if res and res.get("status"):
                        entry.link = res.get("decoded_url")
                elif hasattr(googlenewsdecoder, 'decode'):
                    res = googlenewsdecoder.decode(entry.link)
                    if isinstance(res, dict) and res.get("status"):
                        entry.link = res.get("decoded_url")
                time.sleep(0.5)
            except Exception:
                pass
    
        status = "후보"
        if any(domain in entry.link for domain in EXCLUDE_DOMAINS):
            status = "제외-필터"
        else:
            norm_link = normalize_link(entry.link)
            if norm_link in existing_links:
                status = "제외-기수집"
            else:
                entry.link = norm_link
                candidate_entries.append(entry)
                
        print(f" {i+1}. [{status}] {entry.title} ({entry.get('published', '')})")
        print(f"    URL: {entry.link}")
    
    if candidate_entries:
        title_only_text = "\n".join([f"{idx}: {entry.title}" for idx, entry in enumerate(candidate_entries)])
        print("-" * 50)
        prompt1 = config["prompt1"].format(count=len(candidate_entries), title_only_text=title_only_text, recent_titles_text=recent_titles_text)
        
        max_retries = 3
        stop_collection = False
        
        for attempt in range(max_retries):
            try:
                response1 = client.models.generate_content(
                    model="gemini-3.1-flash-lite",
                    contents=prompt1
                )
                
                res1_text = response1.text.strip()
                
                print(f"   -> 1차 제미나이 응답 원본: {res1_text}")
                
                if res1_text.startswith("```json"):
                    res1_text = res1_text[7:-3].strip()
                elif res1_text.startswith("```"):
                    res1_text = res1_text[3:-3].strip()
                    
                data = json.loads(res1_text)
                valid_ids = []
                if isinstance(data, list):
                    for item in data:
                        idx = item.get("id")
                        if idx is not None and isinstance(idx, int) and 0 <= idx < len(candidate_entries):
                            valid_ids.append(idx)
                    editorial_note = "종합 편집자 브리핑 정보가 제공되지 않았습니다."
                else:
                    selected_ids = data.get("selected_ids", [])
                    for idx in selected_ids:
                        if idx is not None and isinstance(idx, int) and 0 <= idx < len(candidate_entries):
                            valid_ids.append(idx)
                    editorial_note = data.get("editorial_note", "종합 편집자 브리핑 정보가 누락되었습니다.")
                
                if valid_ids:
                    print(f"\n총 {len(valid_ids)}개의 엄선된 기사 본문을 Trafilatura로 추출합니다...")
                    selected_news_parts = []
                    log_news_parts = []
                    final_valid_ids = []
                    for idx in valid_ids:
                        entry = candidate_entries[idx]
                        print(f" - [{idx}] {entry.title}")
                        
                        try:
                            final_url = entry.link
                            try:
                                if hasattr(googlenewsdecoder, 'new_decoderv1'):
                                    res = googlenewsdecoder.new_decoderv1(entry.link)
                                    if res and res.get("status"):
                                        final_url = res.get("decoded_url")
                                    else:
                                        msg = res.get("message", "Unknown error") if res else "No response"
                                        print(f"   -> [경고] 구글 뉴스 URL 디코딩 실패: {msg}")
                                elif hasattr(googlenewsdecoder, 'decode'):
                                    res = googlenewsdecoder.decode(entry.link)
                                    if isinstance(res, dict) and res.get("status"):
                                        final_url = res.get("decoded_url")
                                    else:
                                        msg = res.get("message", "Unknown error") if isinstance(res, dict) else "No response"
                                        print(f"   -> [경고] 구글 뉴스 URL 디코딩 실패: {msg}")
                            except Exception as e:
                                print(f"   -> [에러] 구글 뉴스 URL 디코딩 중 예외 발생: {e}")
                            
                            html_content = None
                            headers = {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                                'Connection': 'keep-alive',
                                'Upgrade-Insecure-Requests': '1'
                            }
                            try:
                                # Try with requests library first (better header handling)
                                import requests
                                response = requests.get(final_url, headers=headers, timeout=20, verify=False)
                                if response.status_code == 200:
                                    # 인코딩이 ISO-8859-1로 잘못 추정되는 한글 사이트 등의 오류를 apparent_encoding으로 보정
                                    if response.encoding == 'ISO-8859-1':
                                        response.encoding = response.apparent_encoding
                                    html_content = response.text
                                else:
                                    raise RuntimeError(f"HTTP {response.status_code}")
                            except Exception as req_err:
                                print(f"   -> requests 접속 실패({req_err}). urllib으로 2차 접속을 시도합니다.")
                                # Fallback to urllib
                                try:
                                    req = urllib.request.Request(final_url, headers=headers)
                                    with urllib.request.urlopen(req, timeout=20) as response:
                                        html_content = response.read().decode('utf-8', errors='ignore')
                                except Exception as url_err:
                                    print(f"   -> urllib 접속 실패({url_err}). cloudscraper로 3차 접속을 시도합니다.")
                                    # Fallback to cloudscraper
                                    try:
                                        import cloudscraper
                                        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
                                        response = scraper.get(final_url, headers=headers, timeout=20)
                                        if response.status_code == 200:
                                            # 인코딩 보정
                                            if response.encoding == 'ISO-8859-1':
                                                response.encoding = response.apparent_encoding
                                            html_content = response.text
                                        else:
                                            raise RuntimeError(f"HTTP {response.status_code}")
                                    except Exception as cs_err:
                                        raise RuntimeError(f"Fetch failed. Requests: {req_err}, Urllib: {url_err}, Cloudscraper: {cs_err}")
                                
                            if html_content:
                                detail_title = extract_title_from_detail(html_content)
                                if detail_title and is_valid_title_similarity(entry.title, detail_title):
                                    suffix = get_publisher_suffix(entry.title)
                                    if suffix and not detail_title.endswith(suffix):
                                        entry.title = detail_title + suffix
                                    else:
                                        entry.title = detail_title

                            content = trafilatura.extract(html_content)
                            if content and len(content.strip()) > 100:
                                print(f"   -> Trafilatura 추출 성공 (길이: {len(content)}자)")
                                content = content[:4000]
                            else:
                                raise ValueError(f"본문 추출 결과가 비어있거나 너무 짧음 ({len(content.strip()) if content else 0}자)")
                        except Exception as e:
                            if html_content:
                                print(f"   -> Trafilatura 추출 실패({e}). Newspaper4k로 2차 추출을 시도합니다.")
                                try:
                                    article = Article(url=entry.link)
                                    article.set_html(html_content)
                                    article.parse()
                                    content = article.text
                                    
                                    if not content or len(content.strip()) <= 100:
                                        raise ValueError(f"Newspaper4k 추출 결과가 비어있거나 너무 짧음 ({len(content.strip()) if content else 0}자)")
                                        
                                    print(f"   -> Newspaper4k 추출 성공 (길이: {len(content)}자)")
                                    content = content[:4000]
                                except Exception as news_e:
                                    print(f"   -> Newspaper4k 추출 실패({news_e}). BeautifulSoup으로 3차 추출을 시도합니다.")
                                    try:
                                        soup = BeautifulSoup(html_content, 'html.parser')
                                        for noise in soup(['script', 'style', 'nav', 'footer', 'aside', 'header']):
                                            noise.decompose()
                                        
                                        main_area = soup.find('article')
                                        if not main_area:
                                            main_area = soup.find(class_=lambda c: c and any(x in c.lower() for x in ['article-body', 'post-content', 'entry-content', 'content']))
                                        if not main_area:
                                            main_area = soup
                                        
                                        paragraphs = main_area.find_all('p')
                                        content = '\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
                                        
                                        if not content or len(content.strip()) <= 100:
                                            raise ValueError(f"BeautifulSoup 추출 결과도 비어있거나 너무 짧음 ({len(content.strip()) if content else 0}자)")
                                            
                                        print(f"   -> BeautifulSoup 추출 성공 (길이: {len(content)}자)")
                                        content = content[:4000]
                                    except Exception as bs_e:
                                        content = f"본문 수집 실패 (사유: {bs_e}) - 제목으로만 요약"
                                        print(f"   -> 추출 최종 실패: {bs_e}")
                            else:
                                content = f"웹페이지 접속 실패 (사유: {e}) - 제목으로만 요약"
                                print(f"   -> 접속 에러/실패: {e}")
                            
                        is_paywalled = False
                        if content and not content.startswith("본문 수집 실패") and not content.startswith("웹페이지 접속 실패"):
                            content_lower = content.lower()
                            if any(keyword in content_lower for keyword in PAYWALL_KEYWORDS):
                                is_paywalled = True
                                
                        if is_paywalled:
                            print(f"   -> 🚨 페이월(유료 기사) 감지됨. 요약에서 제외합니다.")
                            time.sleep(1)
                            continue
                            
                        selected_news_parts.append(f"{idx}: [제목] {entry.title}\n[본문 일부]\n{content}")
                        log_news_parts.append(f"{idx}: [제목] {entry.title}\n[본문 일부]\n{content[:500]}" + ("..." if len(content) > 500 else ""))
                        final_valid_ids.append(idx)
                        time.sleep(1)
                        
                    if not final_valid_ids:
                        print("   -> 🚨 유효한 기사 본문이 남지 않아 2차 요약을 생략합니다.")
                        break

                    selected_news_text = "\n\n".join(selected_news_parts)
                    log_news_text = "\n\n".join(log_news_parts)
        
                    print("\n[추출 본문 확인 (최대 500자)]")
                    print(log_news_text)
                    print("-" * 50)
                    prompt2 = config["prompt2"].format(count=len(final_valid_ids), selected_news_text=selected_news_text)
                    response2 = client.models.generate_content(
                        model="gemini-3.1-flash-lite",
                        contents=prompt2
                    )
                    
                    res2_text = response2.text.strip()
                    
                    print(f"   -> 2차 제미나이 응답 원본: {res2_text}")
                    
                    if res2_text.startswith("```json"):
                        res2_text = res2_text[7:-3].strip()
                    elif res2_text.startswith("```"):
                        res2_text = res2_text[3:-3].strip()
                        
                    summarized_items = json.loads(res2_text)
                    
                    summary_key = "summary_kr" if config["lang_name"] == "Korean" else "summary_en"
                    for item in summarized_items:
                        idx = item.get("id")
                        summary = item.get(summary_key, "")
                        if idx in final_valid_ids:
                            entry = candidate_entries[idx]
                            
                            pub_date = entry.get("published", "")
                            timestamp_ms = None
                            if pub_date:
                                try:
                                    dt = email.utils.parsedate_to_datetime(pub_date)
                                    timestamp_ms = int(dt.timestamp() * 1000)
                                except Exception:
                                    pass
                            if timestamp_ms is None:
                                timestamp_ms = int(time.time() * 1000)

                            human_readable_date = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d')
                            new_articles.append({
                                "title": entry.title,
                                summary_key: summary,
                                "link": entry.link,
                                "timestamp": timestamp_ms,
                                "date": human_readable_date
                            })
                
                
                break
                
            except Exception as e:
                wait_time = 60

                if attempt < max_retries - 1:
                    print(f"[{config['lang_name']}] Gemini API 호출 오류 발생. {wait_time}초 대기 후 재시도합니다... ({attempt+1}/{max_retries})")
                    print(f"👉 상세 오류 내용: {e}")
                    time.sleep(wait_time)
                else:
                    print(f"[{config['lang_name']}] Gemini API 최종 실패: {e}")
                    print("\n[조기 종료] API 최대 재시도 횟수를 초과했습니다. 지금까지 수집된 데이터를 저장하고 작업을 마칩니다...")
                    stop_collection = True
                    break
                    
        if stop_collection:
            break
    
    all_articles = new_articles + all_articles
    all_articles = all_articles[:MAX_TOTAL_ITEMS]
    
    last_update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for i in range(3):
        start_idx = i * MAX_ITEMS_PER_FILE
        end_idx = start_idx + MAX_ITEMS_PER_FILE
        chunk = all_articles[start_idx:end_idx]
        
        file_path = os.path.join(OUTPUT_DIR, f"p{i+1}.json")
        chunk_data = {
            "last_update": last_update_time,
            "articles": chunk
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=4)
