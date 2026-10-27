#!/usr/bin/env python3
"""Parse a compiled DWIN DGUS II (T5/T5L) DWIN_SET: pages (.icl), touch (13*.bin), display (14*.bin).
Usage: parse_dgus.py <DWIN_SET dir> <out dir>
"""
import json, os, struct, sys, io
from PIL import Image, ImageDraw, ImageFont

src, out = sys.argv[1], sys.argv[2]
os.makedirs(os.path.join(out, 'pages'), exist_ok=True)

def find(prefix_num, ext):
    for f in os.listdir(src):
        if f.lower().endswith(ext) and f.split('_')[0] == str(prefix_num):
            return os.path.join(src, f)
    for f in os.listdir(src):
        if f.lower().endswith(ext) and f.startswith(str(prefix_num)):
            return os.path.join(src, f)

be16 = lambda b, o: struct.unpack_from('>H', b, o)[0]

# ---------- pages (.icl containing JPEG backgrounds) ----------
icl_name = [f for f in os.listdir(src) if f.lower().endswith('.icl') and 'screen' in f.lower()][0]
icl = open(os.path.join(src, icl_name), 'rb').read()
pages = {}
offs = {}
for i in range(0, 96):
    off = struct.unpack_from('>I', icl, 0x10 + 4 * i)[0]
    if 0 < off < len(icl): offs[i] = off
starts = sorted(offs.values()) + [len(icl)]
for i, off in offs.items():
    end = starts[starts.index(off) + 1]
    s = icl.find(b'\xff\xd8', off, off + 64)
    try:
        img = Image.open(io.BytesIO(icl[s:end])); img.load(); pages[i] = img.convert('RGB')
    except Exception:
        pass
# fall back to / prefer the BMP sources shipped in DWIN_SET (NN_name.bmp)
for f in os.listdir(src):
    if f.lower().endswith('.bmp') and f.split('_')[0].isdigit():
        pages[int(f.split('_')[0])] = Image.open(os.path.join(src, f)).convert('RGB')

# ---------- touch file (13) ----------
t = open(find(13, '.bin'), 'rb').read()
touch = []
o = 0
while o + 16 <= len(t):
    h = t[o:o + 16]
    if h[:2] == b'\xff\xff': break
    flag, page = h[0], h[1]
    xs, ys, xe, ye, nxt, on, code = struct.unpack_from('>7H', h, 2)
    o += 16
    body = []
    while o + 16 <= len(t) and t[o] == 0xFE:
        body.append(t[o:o + 16]); o += 16
    e = dict(page=page, sound=flag >> 4, flag=flag & 0x0F, x1=xs, y1=ys, x2=xe, y2=ye,
             next_page=None if nxt == 0xFF00 else nxt, press_page=None if on == 0xFF00 else on)
    if code >> 8 in (0xFE, 0xFD):
        ttype = code & 0xFF
        e['kind'] = {0x00: 'data_input', 0x01: 'popup_menu', 0x02: 'increment', 0x03: 'slider',
                     0x05: 'return_key', 0x06: 'text_input', 0xFF: 'none'}.get(ttype, 'type_%02x' % ttype)
        e['upload_on_release'] = (code >> 8) == 0xFD
        if body:
            b = body[0]
            e['vp'] = '0x%04X' % be16(b, 1)
            if ttype == 0x05:
                e['key_value'] = be16(b, 4)
            elif ttype == 0x00:
                # data input: vp, ..., min/max in 3rd block
                if len(body) >= 3:
                    e['min'] = struct.unpack_from('>i', body[2], 2)[0]
                    e['max'] = struct.unpack_from('>i', body[2], 6)[0]
            elif ttype == 0x02:
                e['inc_mode'] = b[4]; e['step'] = be16(b, 6); e['min'] = be16(b, 8); e['max'] = be16(b, 10)
        e['raw'] = [x.hex(' ') for x in body]
    else:
        e['kind'] = 'keyboard_key'
        e['key_code'] = code
        e['char'] = chr(code) if 32 <= code < 127 else None
    touch.append(e)

# ---------- display file (14) ----------
d = open(find(14, '.bin'), 'rb').read()
TYPES = {0x00: 'var_icon', 0x01: 'anim_icon', 0x02: 'slider', 0x03: 'art_digits', 0x04: 'image_anim',
         0x05: 'icon_rotate', 0x06: 'bit_icon', 0x09: 'graphics', 0x0A: 'curve', 0x10: 'number',
         0x11: 'text', 0x12: 'rtc', 0x13: 'hex', 0x14: 'text_scroll', 0x20: 'qr'}
display = []
for p in range(0, 256):
    o = 0x10 + 4 * p
    cnt, _, off = d[o], d[o + 1], be16(d, o + 2)
    if cnt == 0: continue
    base = off  # offsets are absolute byte offsets
    for k in range(cnt):
        r = d[base + 32 * k: base + 32 * (k + 1)]
        if r[0] != 0x5A: continue
        ty = r[1]
        sp, ln, vp, x, y = struct.unpack_from('>5H', r, 2)
        e = dict(page=p, type=TYPES.get(ty, 'type_%02x' % ty), vp='0x%04X' % vp, x=x, y=y,
                 sp=None if sp == 0xFFFF else '0x%04X' % sp, raw=r.hex(' '))
        if ty == 0x10:
            e.update(color='#%04X' % be16(r, 12), font=r[14], size=r[15], align=r[16],
                     int_digits=r[17], dec_digits=r[18], var_type=r[19])
        elif ty == 0x11:
            x1, y1, x2, y2, tl = struct.unpack_from('>5H', r, 14)
            e.update(color='#%04X' % be16(r, 12), box=[x1, y1, x2, y2], text_len=tl, font_x=r[26], font_y=r[27])
            e['x'], e['y'] = x1, y1
        elif ty == 0x00:
            e.update(v_min=be16(r, 12), v_max=be16(r, 14), icon_min=be16(r, 16), icon_max=be16(r, 18), icon_lib=r[20])
        display.append(e)

json.dump(dict(pages=sorted(pages), touch=touch, display=display), open(os.path.join(out, 'map.json'), 'w'), indent=1)

# ---------- overlays ----------
font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 10)
for p, img in pages.items():
    img.save(os.path.join(out, 'pages', 'p%02d.png' % p))
    ov = img.copy().convert('RGBA')
    layer = Image.new('RGBA', ov.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    for e in [x for x in display if x['page'] == p]:
        if e['type'] == 'text' and 'box' in e:
            dr.rectangle(e['box'], outline=(0, 160, 255, 255), fill=(0, 160, 255, 50))
        else:
            dr.rectangle([e['x'], e['y'], e['x'] + 6, e['y'] + 6], fill=(0, 160, 255, 255))
        dr.text((e['x'] + 8, e['y']), e['vp'][2:], font=font, fill=(0, 90, 255, 255))
    for e in [x for x in touch if x['page'] == p]:
        if e['kind'] == 'keyboard_key': continue
        dr.rectangle([e['x1'], e['y1'], e['x2'], e['y2']], outline=(255, 40, 40, 255), width=2, fill=(255, 0, 0, 40))
        lab = e.get('vp', '')[2:] + ('=%d' % e['key_value'] if 'key_value' in e else '')
        if e.get('next_page') is not None: lab += ' >%d' % e['next_page']
        dr.text((e['x1'] + 3, e['y1'] + 2), lab, font=font, fill=(200, 0, 0, 255))
    Image.alpha_composite(ov, layer).convert('RGB').save(os.path.join(out, 'pages', 'p%02d_map.png' % p))

print('pages', sorted(pages))
print('touch entries', len(touch), 'display entries', len(display))
