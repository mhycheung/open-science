#!/usr/bin/env python3
"""The context-jump movie of README.md and docs/index.md: an animated SVG of two tmux panes,
an active jump (left) and a wait jump (right). A scripted replica of Claude Code 2.1.280;
its layout, glyphs and colours follow screens captured from a real session.

    python3 docs/figures/context_jumps.py              # writes context_jumps.svg next to this file
    python3 docs/figures/context_jumps.py OUT.svg 12.5 # a still frame at 12.5 s, for checking
"""
import sys
from pathlib import Path
import textwrap
from html import escape

COLS = 52          # columns per pane
WRAP = COLS - 2    # content width; the margin absorbs font-width differences
ROWS = 28          # content rows per pane
CW, LH = 8.8, 19   # cell width, line height (px) at 14.5px font
PAD = 10
FS = 14.5

C = {
    'fg': '#d0d0d0', 'white': '#ffffff', 'dim': '#949494', 'green': '#87d787',
    'orange': '#d7875f', 'yellow': '#ffd700', 'sep': '#808080', 'pbg': '#3a3a3a',
    'pchev': '#6c6c6c', 'tag': '#5fd7ff', 'bg': '#1c1c1c', 'border': '#5f5f5f',
    'active': '#5faf00', 'bar': '#5faf00', 'barfg': '#000000',
}

TAG = '◀ typed by open-science, not by you'
WAKE = '◀ open-science waker finished'


def L(*segs):
    """A line: list of (text, style). A bare string is fg."""
    out = []
    for s in segs:
        out.append((s, 'fg') if isinstance(s, str) else s)
    return out


def chop(text, n):
    """Wrap at spaces (keeping them, as a terminal does); break a long word only if it must."""
    return textwrap.wrap(text, n, break_on_hyphens=False, drop_whitespace=False) or ['']


class Pane:
    def __init__(self, title, path):
        self.title = title
        self.header = [
            L(('@LOGO', 0), ' ' * 11, ('Claude Code', 'white'), ' v2.1.280'),
            L(('@LOGO', 1), ' ' * 11, ('Opus 5.5 · Claude Max', 'dim')),
            L(('@LOGO', 2), ' ' * 11, (path, 'dim')),
            L(),
        ]
        self.tr = []          # transcript lines
        self.input = ''
        self.input_tag = False
        self.spinner = None   # line or None
        self.status = L('  ', ('▸▸ auto mode on', 'yellow'), (' (shift+tab to cycle)', 'dim'))
        self.frames = []      # (t, rows)

    # transcript helpers ---------------------------------------------------
    def user(self, text, tag=None):
        chunks = chop(text, WRAP - 2)
        lines = [[('❯ ' if i == 0 else '  ', 'pchev'), (c, 'pbg_white')] for i, c in enumerate(chunks)]
        if tag and len(chunks[-1]) + 3 + len(tag) > WRAP:
            lines.append([('@R', tag)])
        elif tag:
            lines[-1].append(('@R', tag))
        self.tr += [L()] + lines

    def tool(self, name, arg, out=None):
        parts = chop(f'{name}({arg})', WRAP - 2)
        self.tr += [L(), L(('●', 'green'), ' ', (name, 'bold'), parts[0][len(name):])]
        for c in parts[1:]:
            self.tr.append(L('  ' + c))
        self.out(out)

    def out(self, paras):
        k = 0
        for para in paras or []:
            for o in textwrap.wrap(para, WRAP - 5, break_on_hyphens=False) or ['']:
                self.tr.append(L(('  └  ' if k == 0 else '     ', 'dim'), o))
                k += 1

    def say(self, text):
        self.tr.append(L())
        for i, s in enumerate(textwrap.wrap(text, WRAP - 2, break_on_hyphens=False)):
            self.tr.append(L(('●' if i == 0 else ' ', 'white'), ' ', s))

    def note(self, *segs):
        self.tr += [L(), L(*segs)]

    def done(self, text):
        self.spinner = None
        self.tr += [L(), L(('✻ ', 'dim'), (text, 'dim'))]

    def clear(self):
        self.tr = []
        self.spinner = None

    # rendering ------------------------------------------------------------
    def rows(self):
        body = self.header + self.tr
        if self.spinner:
            body = body + [L(), self.spinner]
        box = [L(('─' * COLS, 'sep'))]
        for i, c in enumerate(chop(self.input + ('█' if self.input else ''), WRAP - 2)):
            box.append([('❯ ' if i == 0 else '  ', 'fg'), (c, 'white')])
        box.append(L(('─' * COLS, 'sep')))
        box.append(L(('  ' + TAG, 'tag')) if self.input_tag else self.status)
        allr = body + [L()] + box
        if len(allr) > ROWS - 1:          # keep one blank row above the tmux bar
            allr = allr[-(ROWS - 1):]
        return allr + [L()] * (ROWS - len(allr))

    def snap(self, t):
        self.frames.append((t, self.rows()))

    def type(self, t, text, dt=0.06, tag=True):
        """Type text into the input box, char by char (grouped), starting at t."""
        self.input_tag = tag
        step = max(1, len(text) // 12)
        for i in range(step, len(text) + step, step):
            self.input = text[:i]
            self.snap(t)
            t += dt * step
        return t

    def submit(self, t, tag=TAG):
        text = self.input
        self.input, self.input_tag = '', False
        if text == '/clear':
            self.clear()
        self.user(text, tag)
        self.snap(t)


def build():
    A = Pane('1: active jump', '~/sim')
    B = Pane('2: wait jump', '~/sim')
    RUN = lambda w: L(('* ', 'orange'), (f'{w}… (esc to interrupt)', 'orange'))

    # ---------------- left: active jump ----------------
    A.tool('Bash', 'pixi run pytest tests/test_fit.py -q', ['14 passed in 3.21s'])
    A.say('Step 3 is done: the fit converges on all 14 test cases.')
    A.done('Worked for 2m 10s')
    A.snap(0)
    A.tr = A.tr[:-2]
    A.note(('●', 'white'), ' Ran ', ('1', 'bold'), ' stop hook ', ('(ctrl+o to expand)', 'dim'))
    A.out(['Stop hook error: open-science: context is 262,410 tokens, above 250000. '
           'Do an active jump now (skill open-science-context:context-management) ...'])
    A.spinner = RUN('Saving state')
    A.snap(2.5)
    A.say('Context is over the limit. Saving the state before the jump.')
    A.tool('Update', 'tasks/fit/context.md',
           ['Updated tasks/fit/context.md with 9 additions and 14 removals'])
    A.snap(5)
    A.tool('Bash', 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" active tasks/fit/context.md',
           ['Active jump requested. When this turn ends the session is cleared and resumed with:',
            '/open-science-context:continue-context ~/sim/tasks/fit/context.md'])
    A.snap(7.5)
    A.say('State saved to tasks/fit/context.md. Ending the turn for the jump.')
    A.done('Worked for 38s')
    A.snap(9.5)
    t = A.type(12.5, '/clear')            # pause first, so the last lines can be read
    A.submit(t + 0.5)
    t = A.type(t + 1.5, '/open-science-context:continue-context ~/sim/tasks/fit/context.md', dt=0.03)
    A.submit(t + 0.5)
    A.spinner = RUN('Reading')
    A.snap(t + 0.6)
    A.tr.append(L())
    A.tr.append(L('  Read ', ('2', 'bold'), ' files ', ('(ctrl+o to expand)', 'dim')))
    A.snap(t + 2)
    A.say('Resuming task fit from tasks/fit/context.md. Done: steps 1-3. '
          'Next: step 4, the fit on the full data set.')
    A.snap(t + 3.5)
    A.tool('Bash', 'pixi run python analysis/fit.py --all')
    A.spinner = RUN('Running')
    A.snap(t + 5)
    A.out(['fit converged on 2,048 events: chi2/dof = 1.02'])
    A.say('Step 4 is done. Next: step 5, the systematics.')
    A.done('Worked for 1m 12s')
    A.snap(t + 9)
    lapse = t + 9.5

    # ---------------- right: wait jump ----------------
    B.tool('Bash', 'sbatch slurm/scan.sh', ['Submitted batch job 812345'])
    B.spinner = RUN('Submitting')
    B.snap(0)
    B.tool('Bash', 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait_slurm.sh" 812345',
           ['Running in the background (↓ to manage)'])
    B.snap(1.5)
    B.say('The job needs about 3 hours. Saving the state for a wait jump.')
    B.tool('Update', 'tasks/scan/context.md',
           ['Updated tasks/scan/context.md with 6 additions and 2 removals'])
    B.snap(4)
    B.tool('Bash', 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" wait tasks/scan/context.md',
           ['Wait jump requested. When this turn ends the Stop hook checks that something '
            'will wake the new session (a background subagent or shell). If nothing will, '
            'it refuses and tells you.'])
    B.snap(6.5)
    B.say('Job 812345 is queued. Clearing and waiting for it.')
    B.done('Worked for 25s · 1 shell still running')
    B.status = L('  ', ('▸▸ auto mode on', 'yellow'), (' · 1 shell · ↓ to manage', 'dim'))
    B.snap(8.5)
    t = B.type(11.5, '/clear')            # pause first, so the last lines can be read
    B.submit(t + 0.5)
    # idle; the clock runs forward (status bar), then the waker exits
    wake = lapse + 3.5
    B.status = L('  ', ('▸▸ auto mode on', 'yellow'), (' (shift+tab to cycle)', 'dim'))
    B.tr += [L(), L(('●', 'white'), ' Background command "bash …/wait_slurm.sh'),
             L('  812345" completed (exit code 0)'), [('@R', WAKE)]]
    B.spinner = RUN('Thinking')
    B.snap(wake)
    B.tool('Skill', 'open-science-context:continue-context', ['Successfully loaded skill'])
    B.snap(wake + 1.5)
    B.say('Using the context file registered for this pane: tasks/scan/context.md')
    B.tr.append(L())
    B.tr.append(L('  Read ', ('2', 'bold'), ' files, ran ', ('1', 'bold'), ' shell command'))
    B.snap(wake + 3)
    B.say('Job 812345 finished: COMPLETED in 2h 51m. Next: the scan plots.')
    B.tool('Bash', 'pixi run python analysis/plot_scan.py')
    B.spinner = RUN('Running')
    B.snap(wake + 5)

    total = wake + 10
    # status-bar clock: real time until the time-lapse, then fast forward
    clock = [(0, '14:02')]
    for i, hm in enumerate(['14:03', '14:20', '14:48', '15:17', '15:45', '16:14', '16:42', '16:53']):
        clock.append((lapse + i * 0.4, hm))
    return A, B, clock, total


# ---------------------------------------------------------------- SVG output
STY = {
    'fg': f'fill="{C["fg"]}"', 'white': f'fill="{C["white"]}"', 'dim': f'fill="{C["dim"]}"',
    'green': f'fill="{C["green"]}"', 'orange': f'fill="{C["orange"]}"',
    'yellow': f'fill="{C["yellow"]}"', 'sep': f'fill="{C["sep"]}"',
    'pchev': f'fill="{C["pchev"]}"', 'pbg_white': f'fill="{C["white"]}"',
    'bold': f'fill="{C["white"]}" font-weight="bold"', 'tag': f'fill="{C["tag"]}"',
}


LOGO = [' ▐▛███▜▌', '▝▜█████▛▘', '  ▘▘ ▝▝']
QUAD = {'█': '1111', '▐': '0101', '▌': '1010', '▛': '1110', '▜': '1101', '▝': '0100',
        '▘': '1000', ' ': '0000'}   # TL TR BL BR


def logo_svg(k, x0, y):
    """Draw one row of the mascot as filled rectangles (block glyphs leave gaps)."""
    top, h, w = y - LH + 4, LH / 2, CW / 2
    out = []
    for half in (0, 1):
        bits = ''.join(QUAD[ch][2 * half:2 * half + 2] for ch in LOGO[k]) + '0'
        start = None
        for i, b in enumerate(bits):
            if b == '1' and start is None:
                start = i
            elif b == '0' and start is not None:
                out.append(f'<rect x="{x0 + start * w:.1f}" y="{top + half * h:.1f}" '
                           f'width="{(i - start) * w:.1f}" height="{h + 0.6:.1f}" fill="{C["orange"]}"/>')
                start = None
    return ''.join(out)


def row_svg(line, x0, y):
    out, col = [], 0
    if line and line[0] == ('❯ ', 'pchev'):
        out.append(f'<rect x="{x0:.1f}" y="{y - LH + 4}" width="{COLS * CW:.1f}" height="{LH}" fill="{C["pbg"]}"/>')
    tspans = []
    for text, st in line:
        if text == '@LOGO':
            out.append(logo_svg(st, x0, y))
            continue
        if text == '@R':      # right-aligned annotation, style holds its text
            tx = x0 + (WRAP - len(st)) * CW
            tspans.append(f'<tspan x="{tx:.1f}" {STY["tag"]}>{escape(st)}</tspan>')
            continue
        if not text:
            continue
        if st == 'sep':
            out.append(f'<line x1="{x0 + col * CW:.1f}" x2="{x0 + (col + len(text)) * CW:.1f}" '
                       f'y1="{y - 5}" y2="{y - 5}" stroke="{C["sep"]}"/>')
        else:
            tspans.append(f'<tspan x="{x0 + col * CW:.1f}" {STY[st]}>{escape(text)}</tspan>')
        col += len(text)
    if tspans:
        out.append(f'<text y="{y}">' + ''.join(tspans) + '</text>')
    return ''.join(out)


def check(p):
    for t, rows in p.frames:
        for r in rows:
            n = sum(len(s) for s, st in r if s not in ('@R', '@LOGO'))
            n += max([len(st) + 1 for s, st in r if s == '@R'] or [0])
            assert n <= WRAP or r[0][1] == 'sep', (p.title, t, n, ''.join(s for s, _ in r))


def keyframes(name, a, b, total):
    pa, pb = 100 * a / total, 100 * b / total
    if pa <= 0:
        return f'@keyframes {name}{{0%{{opacity:1}}{pb:.3f}%{{opacity:0}}100%{{opacity:0}}}}'
    if pb >= 100:
        return f'@keyframes {name}{{0%{{opacity:0}}{pa:.3f}%{{opacity:1}}100%{{opacity:1}}}}'
    return (f'@keyframes {name}{{0%{{opacity:0}}{pa:.3f}%{{opacity:1}}'
            f'{pb:.3f}%{{opacity:0}}100%{{opacity:0}}}}')


def track(prefix, frames, total, render):
    css, body = [], []
    frames = sorted(frames, key=lambda f: f[0])
    for i, (t, content) in enumerate(frames):
        t2 = frames[i + 1][0] if i + 1 < len(frames) else total
        if t2 <= t:
            continue
        if AT is not None:
            if t <= AT < t2:
                body.append(f'<g>{render(content)}</g>')
            continue
        name = f'{prefix}{i}'
        css.append(keyframes(name, t, t2, total))
        css.append(f'.{name}{{animation:{name} {total}s step-end infinite;opacity:0}}')
        body.append(f'<g class="{name}">{render(content)}</g>')
    return css, body


def main(out):
    A, B, clock, total = build()
    for p in (A, B):
        check(p)
    W = PAD * 2 + (2 * COLS + 1) * CW
    H = PAD * 2 + (ROWS + 2) * LH
    xA = PAD
    xB = PAD + (COLS + 1) * CW
    ytop = PAD + LH           # baseline of the pane-title row
    css, body = [], []

    def pane_render(x0):
        return lambda rows: ''.join(row_svg(r, x0, ytop + (i + 1) * LH) for i, r in enumerate(rows))

    for p, x0, pre in ((A, xA, 'a'), (B, xB, 'b')):
        c, b = track(pre, p.frames, total, pane_render(x0))
        css += c
        body += b

    ybar = ytop + (ROWS + 1) * LH

    def clock_render(hm):
        tx = W - PAD - len(f'"cluster" {hm} 24-Sep-26') * CW
        return f'<text x="{tx:.1f}" y="{ybar}" fill="{C["barfg"]}">"cluster" {hm} 24-Sep-26</text>'

    c, b = track('c', clock, total, clock_render)
    css += c

    static = []
    static.append(f'<rect width="{W:.1f}" height="{H}" rx="6" fill="{C["bg"]}"/>')
    # pane-title borders (tmux pane-border-status top)
    for p, x0, col in ((A, xA, C['active']), (B, xB, C['border'])):
        yl = ytop - 5
        label = f' {p.title} '
        static.append(f'<line x1="{x0:.1f}" x2="{x0 + 2 * CW:.1f}" y1="{yl}" y2="{yl}" stroke="{col}"/>')
        static.append(f'<text x="{x0 + 2 * CW:.1f}" y="{ytop}" fill="{C["fg"]}">{escape(label)}</text>')
        static.append(f'<line x1="{x0 + (2 + len(label)) * CW:.1f}" x2="{x0 + COLS * CW:.1f}" '
                      f'y1="{yl}" y2="{yl}" stroke="{col}"/>')
    xv = xA + COLS * CW + CW / 2
    static.append(f'<line x1="{xv:.1f}" x2="{xv:.1f}" y1="{ytop - 12}" y2="{ybar - LH + 4}" '
                  f'stroke="{C["border"]}"/>')
    # status bar
    static.append(f'<rect x="{PAD}" y="{ybar - LH + 4}" width="{W - 2 * PAD:.1f}" height="{LH}" fill="{C["bar"]}"/>')
    static.append(f'<text x="{PAD}" y="{ybar}" fill="{C["barfg"]}">[sim] 0:claude*</text>')

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H}" '
           f'shape-rendering="crispEdges" viewBox="0 0 {W:.1f} {H}" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,'
           f'\'DejaVu Sans Mono\',\'Liberation Mono\',monospace" font-size="{FS}" '
           f'xml:space="preserve">'
           f'<style>text{{white-space:pre}}{"".join(css)}</style>'
           + ''.join(static) + ''.join(body) + ''.join(b) + '</svg>')
    with open(out, 'w') as f:
        f.write(svg)
    print(f'{out}: {len(svg) / 1024:.0f} KB, {len(A.frames)}+{len(B.frames)} frames, {total}s loop')


AT = None
if __name__ == '__main__':
    if len(sys.argv) > 2:
        AT = float(sys.argv[2])   # static frame at this time, for checking
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name('context_jumps.svg')))
