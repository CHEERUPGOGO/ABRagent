import json, time, urllib.request, urllib.parse

UA = {'User-Agent': 'doi-verify/1.0'}

def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'_error': str(e)}

def show(it):
    t = (it.get('title') or [''])[0]
    c = (it.get('container-title') or [''])[0]
    y = (it.get('issued', {}).get('date-parts') or [[None]])[0][0]
    au = ''
    a = it.get('author') or []
    if a: au = a[0].get('family', '')
    print('   ', it.get('DOI'), '|', y, '|', c, '|', au, '|', t)

for doi in ['10.1021/acsmaterialslett.4c02674']:
    d = get('https://api.crossref.org/works/' + doi)
    if d.get('_error') or d.get('status') != 'ok':
        print('FAIL', doi, '::', d.get('_error'))
    else:
        m = d['message']
        t = (m.get('title') or [''])[0]
        c = (m.get('container-title') or [''])[0]
        y = (m.get('issued', {}).get('date-parts') or [[None]])[0][0]
        au = ''
        a = m.get('author') or []
        if a: au = a[0].get('family', '') + ' et al.'
        print('OK', doi, '|', y, '|', c, '|', au, '|', t)
    time.sleep(0.4)

def q(name, params):
    url = 'https://api.crossref.org/works?' + urllib.parse.urlencode(params) + '&rows=5&select=DOI,title,container-title,issued,author'
    d = get(url)
    print('--', name)
    if d.get('_error'):
        print('   ERROR', d['_error']); return
    for it in d['message']['items']:
        show(it)
    time.sleep(0.4)

q('joule_llm_battery', {'query.author': 'Zuo Xu', 'query.bibliographic': 'large language models battery research'})
q('argonne_rfb_jacs', {'query.author': 'Robertson Shkrob', 'query.bibliographic': 'redox flow batteries solvent stability'})
q('argonne_rfb_jacs2', {'query.author': 'Shkrob', 'query.bibliographic': 'methylphenothiazine solvents robotic'})
print('DONE')
