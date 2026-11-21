import re, urllib.request

html = urllib.request.urlopen('https://arxiv.org/html/2507.16110v1', timeout=60).read().decode('utf-8', 'ignore')
text = re.sub(r'<[^>]+>', ' ', html)
text = re.sub(r'\s+', ' ', text)
print('total chars:', len(text))

def show(kw, width=900, maxhits=4):
    n = 0
    for m in re.finditer(kw, text, re.IGNORECASE):
        s = max(0, m.start() - width // 3)
        print('\n==== kw=', kw, ' @', m.start())
        print(text[s:s + width])
        n += 1
        if n >= maxhits: break
    if n == 0:
        print('\n==== kw=', kw, ' NO MATCH')

show(r'Wet-[Ll]ab Validation', 1400, 2)
show(r'synthesiz', 500, 4)
show(r'coin cell|2032|electrolyte', 400, 2)
show(r'National Research Council|NRC', 300, 2)
show(r'calcination|anneal|sinter', 400, 2)
