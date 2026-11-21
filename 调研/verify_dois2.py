import json, time, urllib.request, urllib.parse

UA = {'User-Agent': 'doi-verify/1.0'}

def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'_error': str(e)}

for doi in ['10.1021/acsmaterialslett.4c02674', '10.1098/rsos.250646']:
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

queries = {
 'argonne_rfb_1': 'Robertson Shkrob stability solvents organic redox flow batteries',
 'argonne_rfb_2': 'uncovering the stability limit charged organic molecules redox flow battery solvents',
}
for name, q in queries.items():
    url = ('https://api.crossref.org/works?query.bibliographic='
           + urllib.parse.quote(q) + '&rows=5&select=DOI,title,container-title,issued,author')
    d = get(url)
    print('--', name)
    if d.get('_error'):
        print('   ERROR', d['_error']); continue
    for it in d['message']['items']:
        t = (it.get('title') or [''])[0]
        c = (it.get('container-title') or [''])[0]
        y = (it.get('issued', {}).get('date-parts') or [[None]])[0][0]
        au = ''
        a = it.get('author') or []
        if a: au = a[0].get('family', '')
        print('   ', it.get('DOI'), '|', y, '|', c, '|', au, '|', t)
    time.sleep(0.4)
print('DONE')
