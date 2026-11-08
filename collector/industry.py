"""Small, event-scoped text evidence for industry classification; no generated facts."""
import re
from bs4 import BeautifulSoup

def normalized(value):return re.sub(r'\W+',' ',str(value or '')).strip().casefold()

def extract(body,title,single_event=False):
 soup=BeautifulSoup(body,'html.parser')
 for n in soup.select('script,style,nav,footer,aside,form,iframe'):n.decompose()
 headings=soup.select('h1'); heading=headings[0].get_text(' ',strip=True) if len(headings)==1 else ''
 if re.search(r'all conferences|upcoming events|event calendar|conference directory',heading,re.I):return ''
 if not heading or (normalized(heading)!=normalized(title) and not single_event):return ''
 # Never borrow prose from a multi-event JSON-LD catalog.
 from .parse_passes import event_nodes
 if len(list(event_nodes(body)))>1:return ''
 main=soup.select_one('main,article') or soup
 chunks=[]
 for element in main.select('p,h2,h3,li')[:120]:
  if any(re.search(r'sponsor|partner|exhibitor.logo',str(parent.get('class',''))+' '+str(parent.get('id','')),re.I) for parent in [element,*element.parents] if getattr(parent,'attrs',None)):continue
  text=element.get_text(' ',strip=True)
  if not 20<=len(text)<=650 or re.search(r'cookie|privacy policy|sponsored by|our sponsors',text,re.I):continue
  if text not in chunks:chunks.append(text)
  if len(' '.join(chunks))>=1800:break
 return ' '.join(chunks)[:2000]

def classify(db,records):
 if not records:return records
 payload=[{'title':r.get('title',''),'summary':r.get('summary',''),'industry_text':r.get('industry_text',''),'categories':r.get('categories',[])} for r in records[:100]]
 result=db.request('POST','rpc/preview_conference_industries',json={'p_records':payload})
 if not isinstance(result,list) or len(result)!=len(payload):raise ValueError('Industry preview response does not match input')
 for record,classification in zip(records,result):
  record['_industry_resolved']=classification.get('status')=='matched'
  metadata=record.setdefault('extraction',{}).setdefault('metadata_enrichment',{})
  for key,default in (('fields',{}),('evidence',{}),('suggestions',{}),('warnings',[])):metadata.setdefault(key,default)
  metadata['industry_classification']=classification
 return records
