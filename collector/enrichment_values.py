"""Validate quoted page evidence before it can become a database field."""
import re
import unicodedata
from .metadata import country_code,region_for,GROUPS
from .parsers import clean_url

def normalized(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value or ''))).strip().casefold()

def normalize_evidence(rows,text,title,url,known):
    fields={};evidence={};warnings=[]
    for row in rows or []:
        if not isinstance(row,dict):continue
        field=row.get('field');value=row.get('value');quote=row.get('quote')
        if not isinstance(value,str) or not isinstance(quote,str) or not 3<=len(quote)<=600:continue
        if normalized(quote) not in normalized(text) or normalized(value) not in normalized(quote):continue
        if normalized(row.get('event_title'))!=normalized(title):continue
        if clean_url(row.get('page_url'))!=clean_url(url):continue
        if normalized(value) in ('unknown','<unknown>','tbd','none','not provided','not stated','unspecified'):continue
        if field=='country':
            value=country_code(value)
        elif field=='region':
            value=value if value in GROUPS else region_for(value)
        elif field=='format':
            value={'in_person':'in-person','in person':'in-person','in-person':'in-person','onsite':'in-person','on-site':'in-person','physical':'in-person','hybrid':'hybrid','virtual':'virtual','online':'virtual'}.get(normalized(value))
        elif field=='price_from':
            if not re.search(r'ticket|pass|registration|admission',quote,re.I):continue
            if normalized(value) in ('free','complimentary','0'):value=0
            elif re.search(r'\bUSD\b|US\$',quote,re.I):
                found=re.search(r'\d[\d,]*(?:\.\d{1,2})?',value)
                value=float(found[0].replace(',','')) if found else None
            else:continue  # The database price is USD; never silently convert currencies.
        elif field=='attendees':
            if not re.search(r'attendees|participants|delegates',quote,re.I):continue
            cleaned=value.replace(',','').strip()
            value=int(cleaned) if re.fullmatch(r'\d{1,7}',cleaned) else None
            if value is not None and not 1<=value<=500000:continue
        elif field=='industry_research':
            if not 3<=len(value)<=60:continue
        elif field in ('city','venue'):
            if not 2<=len(value)<=240:continue
        else:continue
        if value is None:
            warnings.append('Unsupported '+str(field)+' value was not saved');continue
        fields[field]=value
        evidence[field]={'value':value,'method':'claude_quoted_page','confidence':'high','page_url':url,'text':quote}
    country=fields.get('country') or known.get('country')
    derived=region_for(country)
    if derived:
        fields['region']=derived
        evidence['region']={'value':derived,'method':'country_macro_mapping','confidence':'high','page_url':url,'text':str(country)}
    return fields,evidence,warnings

def scope_issue(title):
    if re.search(r'\blunch\s*(?:and|&)\s*learn\b|\bboard election\b|\b(?:election|voting|nomination) (?:deadline|closes|ends)\b',title,re.I):return 'Individual learning webinar or administrative election activity; not a standalone conference'
    if re.search(r'university (?:closed|closure|reopens)|academic calendar|\blast day (?:to|for)\b|commencement|graduation application|faculty and staff.*party|christmas (?:party|break|tree)|\bMLK\b|coffee hour|\(in person session\)|^open registration$|^registration$|deadline to submit',title,re.I):return 'Calendar administration, holiday or individual activity; not a standalone conference'
    if re.search(r'conference|congress|convention|summit|expo|exhibition|trade[ -]show',title,re.I):return None
    if re.search(r'webinar|meetup|workshop|bootcamp|training course|hackathon|breakout session',title,re.I):return 'Possible out-of-scope listing; editorial review required'
    return None

