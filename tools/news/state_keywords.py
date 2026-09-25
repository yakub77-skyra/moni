"""Shared feed + state tables for the India news tools."""

INDIA_NEWS_FEEDS: dict[str, list[str]] = {
    "times_of_india": [
        "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "https://timesofindia.indiatimes.com/rssfeeds/-2128833038.cms",
    ],
    "ndtv": [
        "https://feeds.feedburner.com/ndtvnews-top-stories",
        "https://feeds.feedburner.com/ndtvnews-india-news",
    ],
    "indian_express": ["https://indianexpress.com/feed/"],
    "hindu": ["https://www.thehindu.com/news/national/feeder/default.rss"],
    "hindustan_times": [
        "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
        "https://www.hindustantimes.com/feeds/rss/latest/rssfeed.xml",
    ],
    "india_today": [
        "https://www.indiatoday.in/rss/1206578",
        "https://www.indiatoday.in/rss/1206550",
    ],
    "news18": ["https://www.news18.com/rss/india.xml"],
    "firstpost": [
        "https://www.firstpost.com/commonfeeds/v1/mfp/rss/india.xml",
        "https://www.firstpost.com/commonfeeds/v1/mfp/rss/politics.xml",
    ],
    "deccan_herald": ["https://www.deccanherald.com/feed"],
    "livemint": ["https://www.livemint.com/rss/news"],
    "business_standard": [
        "https://www.business-standard.com/rss/home_page_top_stories.rss",
    ],
}
# Removed outlets (blocked to non-browser clients, would need a headless
# browser to reach):
#   dna_india + tribuneindia  -> HTTP 403
#   theprint                  -> Cloudflare "Just a moment..." challenge
#   scroll.in                 -> serves HTML, not RSS
# firstpost's /rss/*.xml paths serve an HTML directory page; its real
# endpoints live under /commonfeeds/v1/mfp/rss/.

STATE_KEYWORDS: dict[str, list[str]] = {
    "Delhi": ["delhi", "new delhi", "noida"],
    "Rajasthan": ["rajasthan", "jaipur", "udaipur", "jodhpur"],
    "Karnataka": ["karnataka", "bengaluru", "bangalore", "mysuru"],
    "Maharashtra": ["maharashtra", "mumbai", "pune", "nagpur"],
    "Uttar Pradesh": ["uttar pradesh", "lucknow", "kanpur", "varanasi"],
    "Tamil Nadu": ["tamil nadu", "chennai", "coimbatore"],
    "West Bengal": ["west bengal", "kolkata", "howrah"],
    "Gujarat": ["gujarat", "ahmedabad", "surat", "vadodara"],
    "Kerala": ["kerala", "kochi", "thiruvananthapuram"],
    "Punjab": ["punjab", "ludhiana", "amritsar"],
    "Haryana": ["haryana", "faridabad", "gurgaon", "gurugram"],
    "Bihar": ["bihar", "patna"],
    "Madhya Pradesh": ["madhya pradesh", "bhopal", "indore"],
    "Telangana": ["telangana", "hyderabad"],
    "Andhra Pradesh": ["andhra pradesh", "amaravati", "visakhapatnam"],
    "Odisha": ["odisha", "orissa", "bhubaneswar"],
    "Assam": ["assam", "guwahati"],
    "Jharkhand": ["jharkhand", "ranchi"],
    "Uttarakhand": ["uttarakhand", "dehradun"],
    "Himachal Pradesh": ["himachal", "shimla"],
    "Chhattisgarh": ["chhattisgarh", "raipur"],
    "Goa": ["goa", "panaji", "panjim"],
    "Jammu and Kashmir": ["jammu", "kashmir", "srinagar"],
}

