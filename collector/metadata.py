"""Deterministic event metadata. No AI, no facts taken from search snippets."""
import re
from urllib.parse import urlsplit
import unicodedata
from bs4 import BeautifulSoup

# Product region grouping, not schema.org addressRegion (which is a state/province).
GROUPS={
 'North America':'US:United States|CA:Canada|GL:Greenland|BM:Bermuda',
 'LATAM':'MX:Mexico|BR:Brazil|AR:Argentina|CL:Chile|CO:Colombia|PE:Peru|EC:Ecuador|UY:Uruguay|PY:Paraguay|BO:Bolivia|VE:Venezuela|CR:Costa Rica|PA:Panama|GT:Guatemala|SV:El Salvador|HN:Honduras|NI:Nicaragua|BZ:Belize|CU:Cuba|DO:Dominican Republic|HT:Haiti|JM:Jamaica|TT:Trinidad and Tobago|BS:Bahamas|BB:Barbados|PR:Puerto Rico',
 'Europe':'GB:United Kingdom|IE:Ireland|FR:France|DE:Germany|ES:Spain|PT:Portugal|IT:Italy|NL:Netherlands|BE:Belgium|LU:Luxembourg|CH:Switzerland|AT:Austria|DK:Denmark|NO:Norway|SE:Sweden|FI:Finland|IS:Iceland|PL:Poland|CZ:Czechia|SK:Slovakia|HU:Hungary|RO:Romania|BG:Bulgaria|GR:Greece|CY:Cyprus|MT:Malta|SI:Slovenia|HR:Croatia|BA:Bosnia and Herzegovina|RS:Serbia|ME:Montenegro|MK:North Macedonia|AL:Albania|EE:Estonia|LV:Latvia|LT:Lithuania|UA:Ukraine|MD:Moldova|BY:Belarus|RU:Russia|AD:Andorra|MC:Monaco|SM:San Marino|LI:Liechtenstein',
 'APAC':'CN:China|HK:Hong Kong|MO:Macao|TW:Taiwan|JP:Japan|KR:South Korea|KP:North Korea|IN:India|PK:Pakistan|BD:Bangladesh|LK:Sri Lanka|NP:Nepal|BT:Bhutan|MV:Maldives|SG:Singapore|MY:Malaysia|ID:Indonesia|TH:Thailand|VN:Vietnam|PH:Philippines|KH:Cambodia|LA:Laos|MM:Myanmar|BN:Brunei|TL:Timor-Leste|MN:Mongolia|KZ:Kazakhstan|UZ:Uzbekistan|KG:Kyrgyzstan|TJ:Tajikistan|TM:Turkmenistan|AU:Australia|NZ:New Zealand|FJ:Fiji|PG:Papua New Guinea|WS:Samoa|TO:Tonga|VU:Vanuatu|SB:Solomon Islands',
 'MEA':'TR:Turkey|AE:United Arab Emirates|SA:Saudi Arabia|QA:Qatar|KW:Kuwait|BH:Bahrain|OM:Oman|YE:Yemen|IL:Israel|PS:Palestine|JO:Jordan|LB:Lebanon|SY:Syria|IQ:Iraq|IR:Iran|AM:Armenia|AZ:Azerbaijan|GE:Georgia|AF:Afghanistan|EG:Egypt|MA:Morocco|DZ:Algeria|TN:Tunisia|LY:Libya|SD:Sudan|SS:South Sudan|ZA:South Africa|NG:Nigeria|KE:Kenya|GH:Ghana|ET:Ethiopia|TZ:Tanzania|UG:Uganda|RW:Rwanda|SN:Senegal|CI:Cote d’Ivoire|CM:Cameroon|AO:Angola|ZM:Zambia|ZW:Zimbabwe|BW:Botswana|NA:Namibia|MZ:Mozambique|MG:Madagascar|MU:Mauritius|SC:Seychelles|CD:Democratic Republic of the Congo|CG:Republic of the Congo|ML:Mali|NE:Niger|BF:Burkina Faso|MW:Malawi|GA:Gabon|BJ:Benin|TG:Togo|SL:Sierra Leone|LR:Liberia|GM:Gambia|GN:Guinea|MR:Mauritania|SO:Somalia|DJ:Djibouti|ER:Eritrea|TD:Chad|CF:Central African Republic|GQ:Equatorial Guinea|LS:Lesotho|SZ:Eswatini|CV:Cape Verde|KM:Comoros|ST:Sao Tome and Principe',
}
COUNTRIES={};ALIASES={}
for region,entries in GROUPS.items():
 for item in entries.split('|'):
  code,name=item.split(':');COUNTRIES[code]=(name,region);ALIASES[name.casefold()]=code
ALIASES.update({'usa':'US','u.s.a.':'US','united states of america':'US','uk':'GB','u.k.':'GB','england':'GB','scotland':'GB','wales':'GB','northern ireland':'GB','czech republic':'CZ','türkiye':'TR','turkiye':'TR','republic of korea':'KR','viet nam':'VN','uae':'AE','holland':'NL','hong kong sar':'HK'})
for pair in 'USA:US CAN:CA MEX:MX BRA:BR ARG:AR CHL:CL COL:CO PER:PE GBR:GB IRL:IE FRA:FR DEU:DE ESP:ES PRT:PT ITA:IT NLD:NL BEL:BE CHE:CH AUT:AT DNK:DK NOR:NO SWE:SE FIN:FI POL:PL CZE:CZ HUN:HU GRC:GR ROU:RO CHN:CN HKG:HK TWN:TW JPN:JP KOR:KR IND:IN PAK:PK BGD:BD SGP:SG MYS:MY IDN:ID THA:TH VNM:VN PHL:PH AUS:AU NZL:NZ ARE:AE SAU:SA QAT:QA TUR:TR ZAF:ZA EGY:EG KEN:KE NGA:NG'.split():
 alias,code=pair.split(':');ALIASES[alias.casefold()]=code
# Only unambiguous city/territory names; other cities need country evidence.
CITIES={'Beijing':'CN','Shanghai':'CN','Singapore':'SG','Hong Kong':'HK','Barcelona':'ES','Lisbon':'PT','Budapest':'HU','Helsinki':'FI','Pessac':'FR','Beykoz':'TR','Istanbul':'TR','Tokyo':'JP','Seoul':'KR','Kuala Lumpur':'MY','Bangkok':'TH','Dubai':'AE','Abu Dhabi':'AE','Nairobi':'KE','Cape Town':'ZA'}
def country_code(value):
 value=str(value or '').strip();return value.upper() if value.upper() in COUNTRIES else ALIASES.get(value.casefold())
def region_for(value):
 code=country_code(value);return COUNTRIES[code][1] if code else None

def infer(body,url,title,known=None,node=None,single_event=False):
 known=known or {};node=node or {};fields={};proof={};warnings=[];suggestions={}
 soup=BeautifulSoup(body.decode('utf-8','replace') if isinstance(body,bytes) else body,'html.parser')
 for n in soup.select('script,style,nav,header,footer,aside'):n.decompose()
 main=soup.select_one('main,article') or soup
 text=main.get_text(' ',strip=True)
 # A catalog/other event must not lend facts to this event. Structured facts remain scoped to node.
 headings=[h.get_text(' ',strip=True) for h in main.find_all('h1')]
 dedicated=len(headings)==1 and (re.sub(r'\W','',headings[0]).casefold() in re.sub(r'\W','',title).casefold() or re.sub(r'\W','',title).casefold() in re.sub(r'\W','',headings[0]).casefold())
 scoped=text[:40000] if dedicated or single_event else ''
 def put(key,value,method,evidence,confidence='high'):
  if value is not None:fields[key]=value;proof[key]={'value':value,'method':method,'confidence':confidence,'page_url':url,'text':str(evidence)[:300]}
 code=country_code(known.get('country'))
 if code:put('region',COUNTRIES[code][1],'event_country',known['country'])
 # Location wording and title are stronger than general page mentions or organizer footer addresses.
 contexts=[title]+([h.get_text(' ',strip=True) for h in main.find_all(['h2','h3','h4']) if len(h.get_text(' ',strip=True))<160] if dedicated or single_event else [])+[m.group(0) for m in re.finditer(r'(?:venue|location|held in|held at|takes place in|take place in|conference in|conference at|join us in)\s*[:–-]?\s*.{0,220}',scoped,re.I)]
 found={country_code(name) for context in contexts for name in ALIASES if len(name)>3 and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',context,re.I)};found.discard(None)
 if not code and len(found)==1:
  code=found.pop();put('country',COUNTRIES[code][0],'event_location_text',' | '.join(contexts));put('region',COUNTRIES[code][1],'event_country',COUNTRIES[code][0])
 if not code:
  cities={(city,c) for city,c in CITIES.items() if any(re.search(r'\b'+re.escape(city)+r'\b',ctx,re.I) for ctx in contexts)}
  if len({c for _,c in cities})==1:
   code=next(iter(cities))[1];put('country',COUNTRIES[code][0],'event_city',str(sorted(cities)));put('region',COUNTRIES[code][1],'event_city',str(sorted(cities)))
   if len(cities)==1 and not known.get('city'):put('city',next(iter(cities))[0],'event_location_text',str(sorted(cities)))
 if code and not known.get('city') and not fields.get('city'):
  cities=[city for city,c in CITIES.items() if c==code and any(re.search(r'\b'+re.escape(city)+r'\b',ctx,re.I) for ctx in contexts)]
  if len(cities)==1:put('city',cities[0],'event_location_text',' | '.join(contexts))
 tld=(urlsplit(url).hostname or '').split('.')[-1].upper();tld='GB' if tld=='UK' else tld
 # These ccTLDs are widely used generically. Never treat them as venue evidence.
 if not code and tld in COUNTRIES and tld not in ('CO','ME','AI','IO','TV','FM','CC','WS'):
  suggestions['region']={'value':COUNTRIES[tld][1],'method':'country_domain','confidence':'low','page_url':url};warnings.append('Country domain suggests a region but does not establish the event location.')
 # Negated attendance and online registration are not attendance modes.
 attendance_source=unicodedata.normalize('NFKC',' | '.join(contexts)+' '+scoped+' '+title).translate(str.maketrans({'‐':'-','‑':'-','–':'-','—':'-'}))
 attendance_text=re.sub(r'\b(?:not|no longer)\s+(?:an?\s+)?(?:online|virtual|hybrid|in.person|on.site)\s+(?:event|conference|expo|summit)\b','',attendance_source,flags=re.I)
 attendance_text=re.sub(r'\b(?:online|virtual)\s+conference\s+(?:registration|submission)\b','',attendance_text,flags=re.I)
 mode=str(node.get('eventAttendanceMode',''))
 locations=node.get('location') or [];locations=locations if isinstance(locations,list) else [locations]
 virtual=any(isinstance(x,dict) and x.get('@type')=='VirtualLocation' for x in locations)
 physical=any(isinstance(x,dict) and x.get('address') for x in locations)
 fmt=next((v for k,v in [('Mixed','hybrid'),('Online','online'),('Offline','in_person')] if k+'EventAttendanceMode' in mode),None)
 if not fmt:
  hybrid=bool(re.search(r'\bhybrid\s+(?:event|conference|expo|summit)|(?:event|conference)\s+(?:is|will be)\s+hybrid|(?:in.person|on.site)\s*(?:and|&|\+)\s*online',attendance_text,re.I))
  online=bool(re.search(r'\b(?:virtual|online)\s+(?:event|conference|expo|summit)|(?:event|conference)\s+(?:will be held|takes place|is held)\s+(?:entirely\s+)?online',attendance_text,re.I))
  offline=bool(re.search(r'\bin.person\s+(?:event|conference|expo|summit)|(?:event|conference)\s+(?:is|will be)\s+in.person',attendance_text,re.I))
  if hybrid or virtual and physical:fmt='hybrid'
  elif online or virtual:fmt='online'
  elif offline or physical or known.get('venue') or code and (known.get('city') or fields.get('city')):fmt='in_person'
 if fmt:put('format',fmt,'attendance_mode' if mode else 'event_location_or_text',mode or ' | '.join(contexts))
 kinds=[(r'\btrade[ -]?(?:show|fair)\b','trade-show'),(r'\bexpo(?:sition)?\b|\bexhibition\b','expo'),(r'\bsummit\b','summit'),(r'\bconference\b|\bcongress\b|\bjahrestagung\b','conference')]
 for pattern,value in kinds:
  if re.search(pattern,title,re.I):put('event_kind',value,'event_title',title);break
 from .industry import extract
 topic_text=extract(body,title,single_event)
 if topic_text:put('industry_text',topic_text,'event_page_text',topic_text)
 return {'fields':fields,'evidence':proof,'suggestions':suggestions,'warnings':warnings,'version':1}
