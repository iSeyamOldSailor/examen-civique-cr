"""Black-box checks against a private, temporary site at the production project path."""
import functools
import hashlib
import http.server
import json
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ENGINE = sys.argv[1]
SCOPE = '/examen-civique-cr/'
KEY = 'civicCROfficialFirst2026'
PAYLOAD_SHA = '700399b41168e7718bfe9f6124c4fe313cd3dbf4d940243fb5a90af9e17d7c08'
QUOTAS = {'V.symboles':3,'V.laicite':2,'V.situation':6,'I.democratie':3,'I.organisation':2,'I.europe':1,
          'D.droits':2,'D.devoirs':3,'D.situation':6,'H.histoire':3,'H.geographie':3,'H.patrimoine':2,
          'S.residence':1,'S.sante':1,'S.travail':1,'S.education':1}
VIEWS = ['dashboard', 'lessons', 'bank', 'practice', 'sources', 'saveview']
SUCCESS = []
errors = []


def good(name):
    SUCCESS.append(name)
    print('PASS', name, flush=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, *args):
        pass


@contextmanager
def site(temp):
    folder = temp / 'public' / SCOPE.strip('/')
    folder.mkdir(parents=True)
    for path in ROOT.iterdir():
        if path.name.startswith('.'): continue
        if path.is_file(): shutil.copy(path, folder / path.name)
    (folder.parent/'qa-observer.html').write_text('<!doctype html><title>Service worker lifecycle observer</title>')
    def start(port):
        handler = functools.partial(Handler, directory=folder.parent)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        return server

    server = start(0)
    port = server.server_port

    @contextmanager
    def disconnect(ctx):
        # A stopped origin works across engines, including WebKit where the
        # browser automation's offline flag can abort navigations before the worker.
        nonlocal server
        server.shutdown()
        server.server_close()
        try:
            with socket.socket() as probe:
                assert probe.connect_ex(('127.0.0.1', port)) != 0, 'Origin still reachable'
            if ENGINE == 'chromium': ctx.set_offline(True)
            yield
        finally:
            server = start(port)
            if ENGINE == 'chromium': ctx.set_offline(False)

    try: yield folder, f'http://127.0.0.1:{port}{SCOPE}', disconnect
    finally: server.shutdown(); server.server_close()


def setup(pg, url):
    pg.set_default_timeout(8000)
    pg.emulate_media(reduced_motion='reduce')
    pg.on('pageerror', lambda error: errors.append(str(error)))
    pg.on('dialog', lambda d: d.accept())
    pg.goto(url, wait_until='load')
    ready(pg)


def ready(pg):
    pg.locator('main:not([inert])').wait_for()


def poll(pg, expression, timeout=10000):
    # Playwright's wait_for_function uses string evaluation, which WebKit correctly
    # rejects under the production CSP. Poll through the debugging protocol instead.
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if pg.evaluate(expression): return
        time.sleep(0.10)
    raise AssertionError('Timed out observing ' + expression)


def warm(pg):
    poll(pg, "navigator.serviceWorker.controller && document.querySelector('[data-offline-status]').textContent.includes('Prêt hors connexion')",
                         timeout=15000)


def nav(pg, view):
    pg.locator(f'[data-nav="{view}"]').click()
    assert pg.locator('.view.active').get_attribute('id') == view


def btn(pg, selector):
    pg.locator(selector).click()


def state(pg):
    return pg.evaluate('JSON.parse(backupCode()).progress')


def active(pg):
    return state(pg)['active']


def no_overflow(pg, desc):
    sizes = pg.evaluate('({client: document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
    assert sizes['scroll'] <= sizes['client'] + 1, (desc, sizes)


def discard(pg):
    nav(pg,'practice')
    btn(pg,'#resumePractice button[onclick="discardDraft()"]')


def content_checks(pg, content):
    questions = content['questions']
    assert len(set(q['id'] for q in questions)) == len(questions) == 241
    assert Counter(q['kind'] for q in questions) == {'K':209, 'M':32}
    assert all(len(q['o']) == 4 and 0 <= q['a'] < 4 for q in questions)
    assert len(content['dailyLessons']) == 12
    assert all(l['sections'] and l['focus'] and l['themes'] for l in content['dailyLessons'])
    assert len(content['referenceFiches']) == 16
    raw = pg.locator('#payload').text_content()
    assert hashlib.sha256(raw.encode()).hexdigest() == PAYLOAD_SHA
    good('Original v3 educational payload, 209 knowledge questions, 32 scenarios, 12 lessons, 16 syllabus references')

    # Test render, selection, result and source rendering for every question.
    # Seed through the same quiz entry point the bank buttons call. All interactions below use the DOM.
    evidence = pg.evaluate("""() => {
      const refs = new Set(); let tested=0;
      for (const q of C.questions) {
        startQuiz([q], 'Test', false, false);
        const box=document.querySelector('#quizbox');
        const choices=Array.from(box.querySelectorAll('.opt'));
        if(choices.length!==4 || !choices.every(b=>b.getBoundingClientRect().width>0 &&
          getComputedStyle(b.querySelector('.answer')).visibility==='visible' && b.textContent.trim()) ||
          box.querySelector('.feedback') || box.querySelector('.question').textContent!==q.p) throw Error(q.id+' initial');
        const source=box.querySelector('.sourcebox');
        if(!source?.textContent.trim() || !source.querySelector('a[href^="https:"]')) throw Error(q.id+' source');
        if(!(source.compareDocumentPosition(choices[0]) & Node.DOCUMENT_POSITION_FOLLOWING) || !source.querySelector('a')) throw Error(q.id+' order');
        const right=choices.findIndex(b=>b.querySelector('.answer').textContent===q.o[q.a]);
        if(right<0) throw Error(q.id+' answer');
        choices[(right+1)%4].click();
        if(!box.querySelector('.feedback.bad')) throw Error(q.id+' wrong');
        box.querySelectorAll('.opt')[right].click();
        if(box.querySelector('.feedback.bad') || !box.querySelector('.feedback')?.textContent.includes(q.ex)) throw Error(q.id+' right');
        const ref=q.specificRef || C.referenceFiches[q.evidenceRef];
        if(!box.querySelector('.evidence a') || !ref || !box.querySelector('.evidence')?.textContent.includes(q.quote || ref.summary)) throw Error(q.id+' evidence');
        for(const a of box.querySelectorAll('.sourcebox a,.evidence a')) {
          if(new URL(a.href).protocol!=='https:') throw Error(q.id+' URL'); refs.add(a.href);
        }
        box.querySelector('.qfooter .primary').click();
        const bill=document.querySelector('#resultbox');
        if(bill.querySelector('h1').textContent!=='1/1' || !bill.querySelector('.review .sourcebox a') || !bill.querySelector('.review .evidence a')) throw Error(q.id+' review');
        tested++;
      }
      return {tested, anchoredDestinations:refs.size};
    }""")
    assert evidence['tested']==241
    good(f"All 241 question screens, 964 answer choices, wrong/right scoring, source attribution, explanations and corrected-answer links ({evidence['anchoredDestinations']} referenced destinations)")


def routes(pg, data):
    for view in VIEWS:
        nav(pg,view)
        no_overflow(pg,view)
    nav(pg,'dashboard')
    btn(pg,'#dashboard .hero button:first-child')
    assert pg.locator('#lesson-day-0').evaluate('e=>e===document.activeElement')
    for i, lesson in enumerate(data['dailyLessons']):
        nav(pg,'dashboard')
        btn(pg,f'.daybody button[onclick="openLesson({i})"]')
        node = pg.locator(f'#lesson-day-{i}')
        assert node.locator('h2').text_content()==lesson['title']
        assert node.locator('.lesson-unit').count()==len(lesson['sections'])
        node.locator('.lesson-sources summary').click()
        assert node.locator('.lesson-sources a:visible').count()>0
        if i<11:
            node.get_by_role('button',name='Jour suivant').click()
            assert pg.locator(f'#lesson-day-{i+1}').evaluate('e=>e===document.activeElement')
        nav(pg,'lessons')
        node.locator('.actions button').first.click()
        if i<10: assert active(pg)['label'].startswith('Jour '+str(i+1))
        elif i==10: assert active(pg)['full']
        else: assert active(pg)['recall']
        discard(pg)
    nav(pg,'dashboard')
    btn(pg,'#dashboard .hero button:nth-child(2)')
    assert len(active(pg)['items'])==40
    discard(pg)
    nav(pg,'dashboard')
    btn(pg,'#dashboard .hero button:nth-child(3)')
    ids=[]
    for page in range(14):
        rows=pg.locator('#bankList .bankrow strong').all_text_contents()
        assert len(rows)==(14 if page==13 else 15)
        ids.extend(rows)
        if page<13: btn(pg,'#bankPages button:last-child')
    assert ids==[q['p'] for q in data['questions'] if q['kind']=='K']
    assert pg.locator('#bankPages button:last-child').is_disabled()
    btn(pg,'#bankPages button:first-child')
    for theme in 'VIDHS':
        pg.select_option('#bankTheme', theme)
        assert pg.locator('#bankCount').text_content().startswith(str(sum(q['kind']=='K' and q['t']==theme for q in data['questions']))+' questions')
    pg.select_option('#bankTheme','')
    pg.fill('#bankSearch','xxxx00000')
    assert 'Aucune question' in pg.locator('#bankList').text_content()
    pg.fill('#bankSearch',ids[0])
    btn(pg,'#bankList .bankrow button')
    assert pg.locator('#quizbox .question').text_content()==ids[0]
    discard(pg)
    nav(pg,'sources')
    assert pg.locator('#sourceslist .resource a[href^="https:"]').count()==6
    nav(pg,'dashboard')
    btn(pg,'#dashboard .savepanel button')
    assert pg.locator('.view.active').get_attribute('id')=='saveview'
    nav(pg,'practice')
    btn(pg,'#modes button[onclick="reviewMissed()"]')
    assert not state(pg)['missed']
    for fn,count,extra in [('beginOfficial',12,None),('beginRecall',12,'recall'),('beginScenarios',16,'scenario')]:
        nav(pg,'practice')
        btn(pg,f'#modes button[onclick="{fn}()"]')
        draft=active(pg)
        assert len(draft['items'])==count
        if extra=='recall':
            assert pg.locator('#quizbox .opt').count()==0
            btn(pg,'#quizbox .freecover button')
            assert pg.locator('#quizbox .opt').count()==4 and active(pg)['revealed']=={'0':True}
        if extra=='scenario': assert all(x['id'].startswith('M') for x in draft['items'])
        discard(pg)
    good('All six sections, all 12 lesson shortcuts, next-day and practice routes, three dashboard shortcuts, all 14 bank pages, five filters, search, all training modes')


def distributions(pg):
    observed=pg.evaluate("""() => {
        const original=startQuiz; const counts=[]; const orders=new Set();
        try {
          startQuiz=(items,label,full)=>{if(!full || new Set(items.map(q=>q.id)).size!==40) throw Error('quota');
            const bySub={}; for(const q of items) bySub[q.sub]=(bySub[q.sub]||0)+1;
            counts.push(bySub); orders.add(items.map(q=>q.id).join('|'));};
          for(let i=0;i<1000;i++) startMock();
        } finally {startQuiz=original;}
        return {counts, orders:orders.size};
    }""")
    assert observed['orders']==1000 and all(row==QUOTAS for row in observed['counts'])
    good('1,000 independently checked mock draws with 40 unique IDs, 28 knowledge / 12 scenarios, every one of 16 prescribed quotas, 1,000 distinct orders')


def timing_and_scores(pg, qmap):
    for score,passed in [(31,False),(32,True)]:
        nav(pg,'practice')
        btn(pg,'#modes button[onclick="startMock()"]')
        draft=active(pg)
        chosen={}
        assert pg.locator('#clock').inner_text().startswith(('45:','44:'))
        for i,item in enumerate(draft['items']):
            right=item['order'].index(qmap[item['id']]['a'])
            if i<score or i==score:
                pick=right if i<score else (right+1)%4
                chosen[item['id']]=item['order'][pick]
                pg.locator('#quizbox button.opt').nth(pick).evaluate('e=>e.click()')
            assert pg.locator('#quizbox .feedback').count()==0
            pg.locator('#quizbox .qfooter .primary').evaluate('e=>e.click()')
        assert pg.locator('#resultbox h1').inner_text()==f'{score}/40'
        assert ('atteint' in pg.locator('#resultbox h3').inner_text()) == passed
        assert pg.locator('#resultbox .review').count()==40
        assert pg.locator('#resultbox .review[open]').count()==40-score
        result=state(pg)['history'][0]
        assert all(row['choice']==chosen.get(row['id']) for row in result['review'])
        if score==31:
            btn(pg,'#resultbox button[onclick="startMock()"]')
            assert len(active(pg)['items'])==40
            discard(pg)
    nav(pg,'practice')
    btn(pg,'#modes button[onclick="reviewMissed()"]')
    assert active(pg)['label']=='Mes erreurs'
    discard(pg)
    nav(pg,'lessons')
    btn(pg,'#lesson-day-11 .actions button')
    assert active(pg)['label']=='Mes erreurs'
    discard(pg)
    nav(pg,'practice')
    pg.locator('#history button').first.click()
    assert pg.locator('#resultbox h1').inner_text()=='32/40'
    assert pg.locator('#resultbox details').count()==40
    btn(pg,'#resultbox .actions button:last-child')
    assert pg.locator('#modehome').is_visible()
    good('Scoring threshold 31/40 and 32/40, incorrect/unanswered worth zero, delayed mock correction, 40 reviews, repeat mock, mistake revision and historical corrections')


def persistence(browser_type, temp, folder, url, disconnect):
    profile = temp/'profile'
    opts = {'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True}
    def launch(): return browser_type.launch_persistent_context(str(profile), **opts)
    ctx=launch()
    pg=ctx.new_page()
    setup(pg,url);warm(pg)
    nav(pg,'lessons')
    pg.locator('#lesson-day-3 input').check()
    nav(pg,'bank')
    pg.locator('#bankList button').first.click()
    pg.locator('#quizbox .opt').first.click()
    pg.locator('#quizbox .qfooter .primary').click()
    nav(pg,'practice')
    btn(pg,'#modes button[onclick="startMock()"]')
    pg.locator('#quizbox .opt').nth(2).click()
    btn(pg,'#quizbox .qfooter .primary')
    pg.locator('#quizbox .opt').first.click()
    btn(pg,'#quizbox .qfooter .primary')
    before=state(pg)
    assert len(before['active']['answers'])==2
    assert before['done']==[3] and len(before['history'])==1
    assert before['history'][0]['review']
    nav(pg,'dashboard')
    assert pg.locator('#resumeDashboard button').first.is_visible()
    btn(pg,'#resumeDashboard button:first-child')
    nav(pg,'saveview')
    assert pg.locator('#resumeSave button').first.is_visible()
    btn(pg,'#resumeSave button:first-child')
    nav(pg,'practice')
    btn(pg,'#resumePractice button:first-child')
    ctx.close()
    ctx=launch()
    pg=ctx.new_page()
    setup(pg,url)
    assert active(pg)==before['active'] and state(pg)==before
    assert pg.locator('#quizbox .question').is_visible()
    btn(pg,'#quizbox .qfooter .secondary')
    assert pg.locator('#quizbox .opt.sel').inner_text().startswith('A')
    btn(pg,'#quizbox .qfooter .primary')
    assert active(pg)==before['active']
    warm(pg)
    good('Full persistent browser close/relaunch, identical quiz order, answers, position, start time, lesson and history')
    with disconnect(ctx):
        pg.reload(wait_until='load')
        ready(pg)
        assert state(pg)==before
        assert pg.locator('#quizbox .question').is_visible()
        pg.locator('#quizbox .opt').nth(1).click()
        offline_answer=state(pg)
        assert len(offline_answer['active']['answers'])==3
        p2=ctx.new_page()
        setup(p2,url+'?offline-launch');assert state(p2)==offline_answer
        nav(p2,'lessons'); assert p2.locator('#lesson-day-3 input').is_checked()
        nav(p2,'bank'); assert p2.locator('.bankrow').count()==15
        nav(p2,'sources');assert p2.locator('#sourceslist a').count()==6
        p2.close()
    good('Stopped HTTP origin: offline reload, cold-tab launch, lesson, bank, sources and answer saving')
    # Update a separate temporary copy of the published shell under exactly the same origin.
    sw=folder/'sw.js'
    original=sw.read_text()
    html=folder/'index.html'
    html0=html.read_text()
    html.write_text(html0.replace('<title>','<meta name="qa-release" content="next"><title>',1))
    sw.write_text(original.replace("const RELEASE = '","const RELEASE = 'qa-update-",1))
    pg.evaluate("async()=>{const r=await navigator.serviceWorker.ready; await r.update();}")
    poll(pg, "()=>navigator.serviceWorker.getRegistration().then(r=>Boolean(r.waiting))",timeout=25000)
    assert pg.locator('meta[name=qa-release]').count()==0 and state(pg)==offline_answer
    # An out-of-scope same-origin tab can observe activation without keeping an
    # old controlled client open. Cache creation alone only proves installation.
    observer=ctx.new_page()
    observer.goto(url+'../qa-observer.html')
    for window in ctx.pages:
        if window != observer: window.close()
    poll(observer, """async () => {
      const r=await navigator.serviceWorker.getRegistration('/examen-civique-cr/');
      const names=(await caches.keys()).filter(n=>n.startsWith('examen-civique-cr:'));
      return r?.active?.state==='activated' && !r.waiting && !r.installing &&
        names.length===1 && names[0].includes('qa-update-');
    }""",timeout=25000)
    ctx.close()
    ctx=launch(); pg=ctx.new_page()
    setup(pg,url)
    assert pg.locator('meta[name=qa-release]').count()==1, 'Restart did not receive updated HTML'
    assert state(pg)==offline_answer, 'Saved exam differs after update'
    warm(pg)
    with disconnect(ctx):
        pg.reload(wait_until='load');ready(pg)
        assert pg.locator('meta[name=qa-release]').count()==1 and state(pg)==offline_answer
    good('Real service-worker update staged while an exam was open, new shell activated after close, identical progress and correct offline release')
    nav(pg,'saveview')
    with pg.expect_download() as dl:
        btn(pg,'#saveview button[onclick="exportProgress()"]')
    disk=temp/'backup.json'
    dl.value.save_as(disk)
    code=pg.locator('#backupText').input_value()
    backup=json.loads(code)
    assert json.loads(disk.read_text())==backup and backup['progress']==offline_answer
    btn(pg,'#saveview button[onclick="copyProgress()"]')
    assert pg.locator('#backupText').input_value()
    pg.locator('#importText').fill('{"format":"other"}')
    btn(pg,'#saveview button[onclick^="restoreText"]')
    assert 'Impossible' in pg.locator('#backupMessage').inner_text() and state(pg)==offline_answer
    legacy={'format':'civic-cr-progress','version':1,'progress':{'done':[1,2],'history':[{'date':'01/01/2020','label':'Ancien entraînement','full':False,'score':1,'total':2}],'missed':['K001']}}
    pg.locator('#importText').fill(json.dumps(legacy))
    btn(pg,'#saveview button[onclick^="restoreText"]')
    assert state(pg)==legacy['progress']
    pg.locator('#backupFile').set_input_files(str(disk))
    poll(pg, "document.querySelector('#resumeSave button') !== null")
    assert state(pg)==offline_answer
    btn(pg,'#resumeSave button:first-child')
    assert active(pg)==offline_answer['active']
    # An old start timestamp should expire the mock and create one result only.
    expired=json.loads(code)
    expired['progress']['active']['started']-=46*60*1000
    nav(pg,'saveview');pg.fill('#importText',json.dumps(expired))
    btn(pg,'#saveview button[onclick^="restoreText"]')
    btn(pg,'#resumeSave button:first-child')
    assert pg.locator('#resultbox h1').is_visible() and 'active' not in state(pg)
    assert len(state(pg)['history'])==len(offline_answer['history'])+1
    ctx.close();sw.write_text(original); html.write_text(html0)
    good('JSON download and file restore, visible copyable backup, v3 text import, rejected malformed backup, resumed timer expires and records exactly one result')


def fallback_checks(browser, url):
    for mode, script, label in [
        ('mirror', "Object.defineProperty(window,'indexedDB',{get(){throw new Error('disabled')}});", 'stockage de secours'),
        ('primary', "Object.defineProperty(window,'localStorage',{get(){throw new Error('disabled')}});", 'IndexedDB'),
        ('manual', "for(const k of ['indexedDB','localStorage']) Object.defineProperty(window,k,{get(){throw new Error('disabled')}});", 'Stockage indisponible')
    ]:
        ctx=browser.new_context()
        ctx.add_init_script(script)
        pg=ctx.new_page();setup(pg,url)
        pg.locator('#homeSaveStatus').filter(has_text=label).wait_for()
        pg.locator('#daylist input').nth(4).check()
        nav(pg,'practice');btn(pg,'#modes button[onclick="beginRecall()"]')
        btn(pg,'#quizbox .freecover button')
        pg.locator('#quizbox .opt').nth(1).click()
        expected=state(pg)
        if mode!='manual':
            pg.reload();ready(pg)
            actual=state(pg)
            assert actual==expected, (mode, {k:(expected.get(k),actual.get(k)) for k in expected.keys()|actual.keys() if expected.get(k)!=actual.get(k)})
        else:
            nav(pg,'saveview')
            with pg.expect_download() as dl: btn(pg,'#saveview button[onclick="exportProgress()"]')
            assert dl.value.suggested_filename.endswith('.json')
            assert json.loads(pg.locator('#backupText').input_value())['progress']==expected
        ctx.close()
    legacy_ctx=browser.new_context()
    legacy_ctx.add_init_script("""if(!sessionStorage.getItem('seed')){
      localStorage.setItem('civicCROfficialFirst2026', JSON.stringify({done:[3,9],history:[],missed:['K001']}));
      sessionStorage.setItem('seed','1');
    }""")
    pg=legacy_ctx.new_page();setup(pg,url)
    pg.locator('#homeSaveStatus').filter(has_text='IndexedDB').wait_for()
    assert state(pg)['done']==[3,9] and state(pg)['missed']==['K001']
    pg.evaluate("localStorage.removeItem('civicCROfficialFirst2026')")
    pg.reload();ready(pg)
    assert state(pg)['done']==[3,9] and state(pg)['missed']==['K001']
    legacy_ctx.close()
    good('IndexedDB-only and localStorage-only reload, both blocked with working manual export, and existing v3 localStorage migrated to IndexedDB')


def layout(browser,url):
    for width,height in [(320,568),(375,667),(390,844),(430,932),(844,390),(1280,800)]:
        mobile=width!=1280
        ctx=browser.new_context(viewport={'width':width,'height':height},is_mobile=mobile,has_touch=mobile)
        pg=ctx.new_page();setup(pg,url)
        for view in VIEWS:
            nav(pg,view);no_overflow(pg,f'{view} at {width}')
        nav(pg,'practice');btn(pg,'#modes button[onclick="beginOfficial()"]')
        pg.locator('#quizbox .opt').first.click()
        no_overflow(pg,'question at '+str(width))
        answers=pg.locator('#quizbox .opt .answer')
        assert all(a.is_visible() for a in answers.all())
        assert pg.locator('#quizbox .feedback .evidence a').first.is_visible()
        # Last tab is reachable even when the tab strip itself scrolls horizontally.
        nav(pg,'saveview')
        ctx.close()
    good('Six viewports (320, 375, 390, 430, 844 landscape, 1280 CSS px) on all navigation sections and corrected questions')


def two_tabs(browser, url):
    ctx=browser.new_context()
    a=ctx.new_page();setup(a,url)
    b=ctx.new_page();setup(b,url)
    a.locator('#daylist input').first.check()
    poll(b, "saved.done.includes(0)")
    b.locator('#daylist input').nth(1).check()
    poll(a, "saved.done.includes(1)")
    b.close()
    a.reload();ready(a)
    assert state(a)['done']==[0,1]
    ctx.close()
    good('Two open tabs observe each other’s edits; closing an idle tab preserves newer progress')


def main():
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as pw:
        temp=Path(directory)
        with site(temp) as (folder,url,disconnect):
            engine=getattr(pw,ENGINE)
            browser=engine.launch()
            print('TEST ENGINE',ENGINE,browser.version,flush=True)
            ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            external=[];ctx.on('request',lambda r: external.append(r.url) if r.url.startswith(('http:','https:')) and not r.url.startswith(url) else None)
            pg=ctx.new_page();setup(pg,url);warm(pg)
            contents=json.loads(pg.locator('#payload').text_content())
            qmap={q['id']:q for q in contents['questions']}
            routes(pg,contents)
            distributions(pg)
            timing_and_scores(pg,qmap)
            content_checks(pg,contents)
            assert not external, 'Unexpected external requests from the app'
            assert not errors, str(errors[:3])
            good('No external requests during use and no uncaught page errors')
            ctx.close()
            layout(browser,url)
            fallback_checks(browser,url)
            two_tabs(browser,url)
            browser.close()
            persistence(engine,temp,folder,url,disconnect)
            assert not errors,str(errors[:5])
            good('Entire browser run completed without unhandled page errors')
            print('VALIDATION COMPLETE',ENGINE,len(SUCCESS),'groups',flush=True)


if __name__=='__main__':
    main()
