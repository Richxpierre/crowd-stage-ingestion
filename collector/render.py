"""Optional JS fallback. All network traffic is mediated by the guarded fetcher.

No cookies, logins, forms, external domains, WebSockets or service workers.
"""
from __future__ import annotations
import os
from .automatic import same_site


def render_html(fetcher, url, initial_body):
    if os.environ.get('RENDER_HTML','0') != '1':
        return None
    from playwright.sync_api import sync_playwright, TimeoutError as RenderTimeout
    with sync_playwright() as engine:
        browser=engine.chromium.launch(headless=True)
        try:
            context=browser.new_context(service_workers='block',accept_downloads=False,
                                        user_agent=fetcher.session.headers['User-Agent'])
            context.route_web_socket('**/*',lambda ws: ws.close())
            page=context.new_page()
            page.set_default_timeout(12000)
            count=0
            def route_request(route):
                nonlocal count
                request=route.request
                if (request.method!='GET' or not same_site(request.url,url) or count>=25 or
                        request.resource_type in ('image','media','font') or
                        (request.is_navigation_request() and request.url!=url)):
                    return route.abort()
                count+=1
                if request.url==url:
                    return route.fulfill(status=200,body=initial_body,content_type='text/html')
                try:
                    code,body,headers=fetcher.fetch(request.url)
                    route.fulfill(status=code,body=body,content_type=headers.get('Content-Type','text/plain'))
                except Exception:
                    route.abort()
            context.route('**/*',route_request)
            page.on('popup',lambda popup: popup.close())
            try:
                page.goto(url,wait_until='networkidle',timeout=20000)
            except RenderTimeout:
                pass
            html=page.content().encode()
            if len(html)>8*1024*1024:
                raise ValueError('Rendered HTML exceeds 8 MB')
            return html
        finally:
            browser.close()
