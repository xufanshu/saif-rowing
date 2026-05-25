#!/usr/bin/env python3
"""批量导入BLED比赛报名数据"""
import json, urllib.request, urllib.error

API = 'http://localhost:3000/api/data'

# Get current data
req = urllib.request.Request(API)
with urllib.request.urlopen(req) as r:
    data = json.loads(r.read())

# Member name to ID mapping
member_map = {}
for m in data['members']:
    member_map[m['name']] = m['id']

# Create competition
comp_id = 'comp_bled_2026'
data['competitions'].append({
    'id': comp_id,
    'name': 'BLED比赛',
    'date': '2026-09-09',
    'note': '2026年BLED赛事（9/9-9/12）'
})

# Parse entries
lines = [
    # (项目, 组别, 艇型, 日期, 费用1, 费用2, 自费, 编号, [成员])
    ('双单1', 'MB', '2-', '2026/09/09', 115, 25, '否', 111, ['金淼','刘瓛']),
    ('四双1', 'MB', '4X', '2026/09/09', 220, 100, '否', 132, ['张野','张文涛','吕律扬','李意']),
    ('四双2', 'MB', '4X', '2026/09/09', 220, 100, '否', 132, ['李一娇','王佳旭','侯业旺','张学军']),
    ('四双3', 'MB', '4X', '2026/09/09', 220, 100, '否', 132, ['沈大宇','金淼','朱云','刘瓛']),
    ('单人', 'MB', '1X', '2026/09/10', 70, 25, '否', 208, ['李意']),
    ('单人2', 'MB', '1X', '2026/09/10', 70, 25, '否', 208, ['吴锡盛']),
    ('单人4', 'MB', '1X', '2026/09/10', 70, 25, '否', 208, ['刘瓛']),
    ('单人', 'WA', '1X', '2026/09/10', 70, 25, '否', 222, ['徐铭孺']),
    ('四单', 'MB', '4-', '2026/09/10', 0, 0, '否', 225, ['朱云','金淼','刘瓛','吕律扬']),
    ('双双1', 'MB', '2X', '2026/09/11', 115, 50, '否', 322, ['张学军','侯业旺']),
    ('双双3', 'MB', '2X', '2026/09/11', 115, 50, '否', 322, ['金淼','沈大宇']),
    ('双双5', 'MB', '2X', '2026/09/11', 115, 50, '否', 322, ['李意','朱云']),
    ('双双2', 'MB', '2X', '2026/09/11', 115, 50, '否', 322, ['吴锡盛','王佳旭']),
    ('双双2', 'MA', '2X', '2026/09/12', 115, 50, '否', 402, ['吕律扬','李一娇']),
    ('八单', 'MB', '8+', '2026/09/12', 420, 200, '否', 416, ['张文涛','李一娇','王佳旭','张野','侯业旺','沈大宇','张学军','吴锡盛']),
    ('双双', 'MIXB', '2X', '2026/09/09', 115, 50, '否', 520, ['徐铭孺','李意']),
]

errors = []
for entry in lines:
    name, group, boat_type, date, fee1, fee2, is_self, num, members = entry
    total_fee = fee1 + fee2
    fee_per_person = round(total_fee / len(members), 2) if len(members) > 0 else 0
    event_name = f"{name} {group} {boat_type}"
    
    for mem_name in members:
        mem_id = member_map.get(mem_name)
        if not mem_id:
            errors.append(f"未找到会员: {mem_name}")
            continue
        
        data['registrations'].append({
            'id': f"reg_{len(data['registrations'])+1}_{mem_name}",
            'memberId': mem_id,
            'compId': comp_id,
            'event': event_name,
            'feeType': 'self',  # 否 = 自费
            'amount': fee_per_person
        })

# POST back
body = json.dumps(data).encode('utf-8')
req2 = urllib.request.Request(API, data=body, method='POST')
req2.add_header('Content-Type', 'application/json')
with urllib.request.urlopen(req2) as r:
    result = json.loads(r.read())

print(f"✅ 导入完成")
print(f"  - 比赛: BLED比赛")
print(f"  - 报名条数: {len(lines)} 个项目")
print(f"  - 总报名人次: 从数据看...")

# Count by member
from collections import Counter
member_count = Counter()
for reg in data['registrations']:
    if reg['compId'] == comp_id:
        member_count[reg['memberId']] += 1

print(f"  - 涉及会员数: {len(member_count)}")
for mid, cnt in member_count.most_common():
    m = next((x for x in data['members'] if x['id']==mid), None)
    if m:
        print(f"    - {m['name']}: {cnt}次报名")

if errors:
    print(f"\n⚠️ 错误: {len(errors)}")
    for e in errors:
        print(f"  - {e}")
