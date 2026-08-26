# -*- coding: utf-8 -*-
"""检查真实帖子正文里 img 标签的属性模式"""
import json
import re
import requests

html = requests.get('http://127.0.0.1:8000/mag/circle/v1/forum/threadViewPage',
                    params={'tid': 4231648, 'mag_hide_progress': 1}, timeout=20).text

anchor = html.find('var row =')
start = html.find('{', anchor)
depth = 0
in_str = None
end = -1
i = start
while i < len(html):
    ch = html[i]
    if in_str:
        if ch == '\\':
            i += 2
            continue
        if ch == in_str:
            in_str = None
    else:
        if ch in ('"', "'"):
            in_str = ch
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    i += 1

row = json.loads(html[start:end + 1])
content = row['content']

imgs = re.findall(r'<img\b[^>]*>', content)
print('img 总数:', len(imgs))
attr_count = {}
for tag in imgs:
    for attr in re.findall(r'\b([\w-]+)=', tag):
        attr_count[attr] = attr_count.get(attr, 0) + 1
print('属性分布:', attr_count)

print('\n前 3 个 img 标签原文:')
for t in imgs[:3]:
    print(' ', t[:280])
print('\n最后 2 个:')
for t in imgs[-2:]:
    print(' ', t[:280])

print('\nvideo/iframe 数:', len(re.findall(r'<video', content)), len(re.findall(r'<iframe', content)))
# src 值的分布
srcs = re.findall(r'src="([^"]*)"', content)
from collections import Counter
kinds = Counter()
for s in srcs:
    if s.startswith('http'):
        kinds[s.split('/')[2]] += 1
    elif s == '':
        kinds['(空-lazyload)'] += 1
    else:
        kinds['相对:' + s[:30]] += 1
print('\nsrc 分布:', dict(kinds))
