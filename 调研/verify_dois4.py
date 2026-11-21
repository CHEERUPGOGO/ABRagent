import json, time, urllib.request, urllib.parse

UA = {'User-Agent': 'doi-verify/1.0'}

def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'_error': str(e)}

def q(name, params):
    url = 'https://api.crossref.org/works?' + urllib.parse.urlencode(params) + '&rows=6&select=DOI,title,container-title,issued,author'
    d = get(url)
    print('--', name)
    if d.get('_error'):
        print('   ERROR', d['_error']); return
    for it in d['message']['items']:
        t = (it.get('title') or [''])[0]
        c = (it.get('container-title') or [''])[0]
        y = (it.get('issued', {}).get('date-parts') or [[None]])[0][0]
        au = ''
        a = it.get('author') or []
        if a: au = a[0].get('family', '')
        print('   ', it.get('DOI'), '|', y, '|', c, '|', au, '|', t)
    time.sleep(0.4)

q('joule_aug2025', {'filter': 'from-pub-date:2025-07-01,until-pub-date:2025-09-30,type:journal-article',
                    'query.container-title': 'Joule',
                    'query.bibliographic': 'large language models battery research'})
q('jacs_2026_rfb', {'filter': 'from-pub-date:2025-11-01,until-pub-date:2026-02-28,type:journal-article',
                    'query.container-title': 'Journal of the American Chemical Society',
                    'query.author': 'Robertson',
                    'query.bibliographic': 'redox flow battery solvents'})
print('DONE')
