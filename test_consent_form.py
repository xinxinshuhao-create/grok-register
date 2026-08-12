import re
with open('keys/consent_debug.html', encoding='utf-8') as f:
    html = f.read()

if 'Cloudflare' in html[:500]:
    print('CF BLOCKED')
else:
    print('Real consent page')

# Find all input fields
inputs = re.findall(r'<input[^>]*>', html, re.I)
print(f'Total inputs: {len(inputs)}')
for inp in inputs:
    type_m = re.search(r'type="([^"]+)"', inp, re.I)
    name_m = re.search(r'name="([^"]+)"', inp, re.I)
    value_m = re.search(r'value="([^"]*)"', inp, re.I)
    t = type_m.group(1) if type_m else '?'
    n = name_m.group(1) if name_m else '?'
    v = value_m.group(1) if value_m else '?'
    if t != '?':
        print(f'  type={t:8} name={n:20} value={v[:40]}')

# Find the form
form_m = re.search(r'<form[^>]*action="([^"]*)"', html, re.I)
if form_m:
    print(f'\nForm action: {form_m.group(1)[:80]}')

# Find button/decision elements
btns = re.findall(r'<button[^>]*>.*?</button>', html, re.DOTALL | re.I)
print(f'\nButtons: {len(btns)}')
for btn in btns[:5]:
    print(f'  {btn[:100]}')