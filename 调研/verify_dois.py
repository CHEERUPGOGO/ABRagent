import json, time, urllib.request, urllib.parse

UA = {'User-Agent': 'doi-verify/1.0'}

def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'_error': str(e)}

dois = [
 '10.1038/s41586-023-06792-0',
 '10.1038/s42256-024-00832-8',
 '10.1038/s41586-023-06734-w',
 '10.1038/s41586-020-2442-2',
 '10.1038/s41467-022-32938-1',
 '10.1021/jacsau.5c01628',
 '10.7498/aps.74.20250572',
]

print('=== DOI direct checks ===')
for doi in dois:
    d = get('https://api.crossref.org/works/' + doi)
    if d.get('_error') or d.get('status') != 'ok':
        print('FAIL', doi, '::', d.get('_error'))
    else:
        m = d['message']
        t = (m.get('title') or [''])[0]
        c = (m.get('container-title') or [''])[0]
        y = (m.get('issued', {}).get('date-parts') or [[None]])[0][0]
        flag = ' [UPDATE-TO]' if 'update-to' in m else ''
        print('OK', doi, '|', y, '|', c, '|', t, flag)
    time.sleep(0.4)

queries = {
 'liverpool_robots_2024': 'Autonomous mobile robots for exploratory synthetic chemistry',
 'ruza_polymer_2025': 'Autonomous discovery of polymer electrolyte formulations',
 'el_agente_matter': 'El Agente an autonomous agent for quantum chemistry',
 'llmb_acs': 'LLMB AI Agent for Lithium Metal Battery Research Using Large Language Model',
 'sdl_review': 'Autonomous self-driving laboratories a review of technology',
 'batgpt_chem': 'BatGPT-Chem foundation large model chemical',
 'liver_fibrosis': 'AI-Assisted Drug Re-Purposing for Human Liver Fibrosis',
 'ustc_mars': 'Automated synthesis of oxygen-producing catalysts Martian meteorite robotic AI chemist',
 'argonne_rfb': 'methylphenothiazine solvent stability redox flow battery high-throughput',
 'oae_review': 'From large language models to AI agents in energy',
 'argonne_llm_roadmap': 'large language models battery research roadmap',
}

print('=== Bibliographic queries ===')
for name, q in queries.items():
    url = ('https://api.crossref.org/works?query.bibliographic='
           + urllib.parse.quote(q) + '&rows=3&select=DOI,title,container-title,issued,author')
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
        if a:
            au = (a[0].get('family') or '')
        print('   ', it.get('DOI'), '|', y, '|', c, '|', au, '|', t)
    time.sleep(0.4)
print('DONE')
