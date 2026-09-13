'use strict';
'require baseclass';
'require fs';

/*
 * Status -> Overview "Mesh": the neon mesh as a live map -- internet, base, satellites in hop order --
 * with animated links (blue towards devices, amber towards the internet, faster with more traffic),
 * the node you are browsing and the node your own device is on, and every connected device with the
 * node it is on. Data: /usr/sbin/neon-fleet stats (this unit directly, the others over /cgi-bin/neon-node);
 * units that do not answer are simply not drawn. Rates come from successive byte counters.
 *
 * The Overview page re-runs render() on every poll and replaces the section with what it returns, unless
 * that is null. So the DOM is built once and updated in place, and render() returns null afterwards --
 * otherwise the link animation would restart every few seconds.
 * No String.prototype.format here: LuCI 26 moved it to cbi.js (see luci-theme-aurora/VENDORED.md).
 */

const CARD_W = 178, CARD_H = 96, ROW = 124;

const CSS = `
.nm{font:12px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-variant-numeric:tabular-nums;
 --nm-line:color-mix(in srgb,currentColor 13%,transparent);--nm-soft:color-mix(in srgb,currentColor 4%,transparent);
 --nm-mute:color-mix(in srgb,currentColor 58%,transparent);--nm-down:#3b82f6;--nm-up:#f59e0b;--nm-ok:#22c55e;--nm-fair:#eab308;
 --nm-warn:#f97316;--nm-bad:#ef4444;--nm-accent:#6366f1;--nm-card:#fff}
.nm *{box-sizing:border-box}
.nm-bar{display:flex;flex-wrap:wrap;align-items:center;gap:6px 22px;margin:0 0 10px}
.nm-kpi{display:flex;align-items:baseline;gap:6px;white-space:nowrap}
.nm-kpi b{font-size:14px;font-weight:600}
.nm-kpi .l{color:var(--nm-mute)}
.nm-kpi .d{color:var(--nm-down)} .nm-kpi .u{color:var(--nm-up)}
.nm-map{position:relative;border:1px solid var(--nm-line);border-radius:10px;overflow-x:auto;overflow-y:hidden;
 background-image:radial-gradient(var(--nm-line) 1px,transparent 1.3px);background-size:18px 18px}
.nm-stage{position:relative}
.nm-svg{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
.nm-node{position:absolute;width:${CARD_W}px;height:${CARD_H}px;padding:9px 10px 8px;border:1px solid var(--nm-line);border-radius:11px;
 background:var(--nm-card);box-shadow:0 1px 2px rgba(0,0,0,.05),0 6px 16px -6px rgba(0,0,0,.12);cursor:pointer;
 transition:left .45s ease,top .45s ease,border-color .15s,box-shadow .15s}
.nm-node:hover{border-color:color-mix(in srgb,var(--nm-accent) 45%,transparent)}
.nm-node.sel{border-color:var(--nm-accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--nm-accent) 22%,transparent),0 6px 16px -6px rgba(0,0,0,.12)}
.nm-node.self{border-color:color-mix(in srgb,var(--nm-accent) 60%,transparent)}
.nm-head{display:flex;align-items:center;gap:8px;min-width:0}
.nm-ico{flex:none;width:28px;height:28px;color:inherit}
.nm-ico svg{display:block;width:28px;height:28px}
.nm-t{min-width:0;flex:1}
.nm-name{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nm-sub{color:var(--nm-mute);font-size:10.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nm-dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--nm-ok);align-self:flex-start;margin-top:3px;
 animation:nm-pulse 2.6s ease-out infinite;--nm-c:var(--nm-ok)}
.nm-dot.fair{background:var(--nm-fair);--nm-c:var(--nm-fair)} .nm-dot.warn{background:var(--nm-warn);--nm-c:var(--nm-warn)}
.nm-dot.bad{background:var(--nm-bad);--nm-c:var(--nm-bad)}
@keyframes nm-pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--nm-c) 55%,transparent)}70%,100%{box-shadow:0 0 0 7px transparent}}
.nm-rates{display:flex;gap:12px;margin-top:8px;font-size:11.5px;white-space:nowrap}
.nm-rates .d{color:var(--nm-down)} .nm-rates .u{color:var(--nm-up)} .nm-rates small{color:var(--nm-mute);font-size:10px}
.nm-foot{display:flex;justify-content:space-between;margin-top:3px;font-size:10.5px;color:var(--nm-mute);white-space:nowrap}
.nm-tags{position:absolute;left:10px;top:-9px;display:flex;gap:4px}
.nm-tag{padding:0 6px;border-radius:8px;font-size:10px;line-height:16px;font-weight:600;color:#fff;background:var(--nm-accent);
 box-shadow:0 0 0 2px var(--nm-card)}
.nm-tag.you{background:#16a34a}
.nm-inet{position:absolute;width:84px;text-align:center;transition:left .45s ease,top .45s ease}
.nm-globe{width:46px;height:46px;margin:0 auto 4px;border-radius:50%;display:grid;place-items:center;border:1px solid var(--nm-line);
 background:var(--nm-card);color:var(--nm-down)}
.nm-inet .nm-sub{text-align:center}
.nm-lbl{position:absolute;transform:translate(-50%,-50%);padding:1px 7px;border-radius:9px;font-size:10px;white-space:nowrap;
 background:var(--nm-card);border:1px solid var(--nm-line);pointer-events:auto;transition:left .45s ease,top .45s ease}
.nm-lbl b{font-weight:600}
.nm-list{margin-top:12px}
.nm-filter{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px}
.nm-filter .l{color:var(--nm-mute);margin-right:4px}
.nm-chip{border:1px solid var(--nm-line);border-radius:11px;padding:1px 9px;font:inherit;font-size:11px;cursor:pointer;background:transparent;color:inherit}
.nm-chip:hover{background:var(--nm-soft)}
.nm-chip.on{background:var(--nm-accent);border-color:var(--nm-accent);color:#fff}
.nm-tbl{width:100%;border-collapse:collapse}
.nm-tbl th{font-weight:500;text-align:left;font-size:10.5px;letter-spacing:.02em;text-transform:uppercase;color:var(--nm-mute);
 padding:6px 8px;border-bottom:1px solid var(--nm-line);white-space:nowrap}
.nm-tbl td{padding:5px 8px;border-bottom:1px solid var(--nm-line);white-space:nowrap;vertical-align:middle}
.nm-tbl tr:last-child td{border-bottom:0}
.nm-tbl tbody tr:hover td{background:var(--nm-soft)}
.nm-dev{display:flex;align-items:center;gap:8px}
.nm-av{flex:none;width:22px;height:22px;border-radius:6px;display:grid;place-items:center;font-size:10.5px;font-weight:600;color:#fff}
.nm-dev b{font-weight:600} .nm-dev small{display:block;color:var(--nm-mute);font-size:10.5px;line-height:1.2}
.nm-me{margin-left:6px;padding:0 5px;border-radius:7px;font-size:10px;line-height:15px;font-weight:600;color:#fff;background:#16a34a}
.nm-band{margin-left:6px;padding:0 5px;border-radius:4px;border:1px solid var(--nm-line);font-size:10px;color:var(--nm-mute)}
.nm-bars{display:inline-flex;align-items:flex-end;gap:1.5px;height:10px;margin-right:6px;vertical-align:-1px}
.nm-bars i{width:3px;border-radius:1px;background:var(--nm-line)} .nm-bars i.on{background:currentColor}
.nm-d{color:var(--nm-down)} .nm-u{color:var(--nm-up)} .nm-m{color:var(--nm-mute)}
.nm-empty{padding:16px;text-align:center;color:var(--nm-mute)}
`;

const ICON_NODE = led => `<svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="nm-g" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#d7dce5"/></linearGradient></defs>
<ellipse cx="16" cy="28.3" rx="8.5" ry="1.6" fill="currentColor" opacity=".12"/>
<rect x="7" y="3.5" width="18" height="24" rx="7.5" fill="url(#nm-g)" stroke="currentColor" stroke-opacity=".28"/>
<path d="M9.5 9.5h13" stroke="currentColor" stroke-opacity=".12"/><circle cx="16" cy="21.5" r="1.5" fill="${led}"/></svg>`;

const ICON_GLOBE = `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.9 5.6 3.9 9s-1.3 6.4-3.9 9c-2.6-2.6-3.9-5.6-3.9-9S9.4 5.6 12 3z"/></svg>`;

const LIGHT = { green: 'ok', yellow: 'warn', cyan: 'fair', red: 'bad', magenta: 'fair', white: 'fair', blue: 'fair' };
const LED = { ok: '#22c55e', fair: '#eab308', warn: '#f97316', bad: '#ef4444' };
const AVATAR = [ '#6366f1', '#0ea5e9', '#14b8a6', '#f59e0b', '#ec4899', '#8b5cf6', '#10b981', '#f43f5e' ];

function num(bps) {
	if (bps == null || isNaN(bps)) return '–';
	const m = bps / 1e6;
	return m >= 100 ? m.toFixed(0) : m >= 1 ? m.toFixed(1) : m.toFixed(2);
}
function dur(s) {
	s = Math.max(0, Math.floor(s));
	const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
	return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}
function quality(dbm) {
	if (dbm == null || dbm === 0) return { word: _('Unknown'), cls: 'fair', bars: 0 };
	if (dbm >= -60) return { word: _('Excellent'), cls: 'ok', bars: 4 };
	if (dbm >= -68) return { word: _('Good'), cls: 'ok', bars: 3 };
	if (dbm >= -76) return { word: _('Fair'), cls: 'fair', bars: 2 };
	if (dbm >= -84) return { word: _('Weak'), cls: 'warn', bars: 1 };
	return { word: _('Poor'), cls: 'bad', bars: 0 };
}
function bars(dbm) {
	const q = quality(dbm), el = E('span', { 'class': 'nm-bars', 'style': `color:${LED[q.cls]}` });
	[4, 6, 8, 10].forEach((h, i) => el.appendChild(E('i', { 'class': i < q.bars ? 'on' : '', 'style': `height:${h}px` })));
	return el;
}
function lc(s) { return (s || '').toLowerCase(); }

// the first ancestor with an opaque background decides the card colour, so cards cover the links and
// follow whatever light or dark scheme the theme is in right now
function surface(el) {
	for (let n = el; n && n.nodeType == 1; n = n.parentElement) {
		const m = getComputedStyle(n).backgroundColor.match(/rgba?\(([^)]+)\)/);
		if (m) {
			const p = m[1].split(',').map(parseFloat);
			if (p.length < 4 || p[3] > 0.6) return `rgb(${p[0]},${p[1]},${p[2]})`;
		}
	}
	const c = (getComputedStyle(el).color.match(/\d+/g) || [0, 0, 0]).slice(0, 3).reduce((a, b) => a + +b, 0);
	return c > 382 ? '#16181d' : '#ffffff';
}

return baseclass.extend({
	title: _('Mesh'),

	load() {
		const tasks = [
			fs.exec('/usr/sbin/neon-fleet', [ 'stats' ]).then(res => {
				try { return JSON.parse(res.stdout); }
				catch (e) { return { error: (res.stderr || res.stdout || '').trim() || _('neon-fleet returned no data') }; }
			}, err => ({ error: err.message }))
		];
		if (this.viewer === undefined)
			tasks.push(fetch('/cgi-bin/neon-node?whoami', { credentials: 'same-origin', cache: 'no-store' })
				.then(r => r.ok ? r.json() : null).then(j => j && j.ip || null).catch(() => null));
		return Promise.all(tasks);
	},

	render(data) {
		if (!data) return null;
		if (data.length > 1) this.viewer = data[1];

		if (!this.root) this.build();
		this.update(data[0] || {});

		// first render, or the section was hidden and shown again: hand the DOM (back) to the page
		return this.root.isConnected ? null : this.root;
	},

	build() {
		if (!document.getElementById('nm-css'))
			document.head.appendChild(E('style', { 'id': 'nm-css' }, CSS));

		this.prev = new Map();
		this.cards = new Map();
		this.links = new Map();
		this.flows = [];
		this.selected = null;

		this.kpis = E('div', { 'class': 'nm-bar' });
		this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
		this.svg.setAttribute('class', 'nm-svg');
		this.stage = E('div', { 'class': 'nm-stage' }, [ this.svg ]);
		this.map = E('div', { 'class': 'nm-map' }, [ this.stage ]);
		this.filter = E('div', { 'class': 'nm-filter' });
		this.tbody = E('tbody');
		this.list = E('div', { 'class': 'nm-list' }, [
			this.filter,
			E('table', { 'class': 'nm-tbl' }, [
				E('thead', {}, E('tr', {}, [ _('Device'), _('Connected to'), _('Signal'), _('Link rate'), _('Traffic'), _('Online') ]
					.map(h => E('th', {}, h)))),
				this.tbody
			])
		]);
		this.root = E('div', { 'class': 'nm' }, [ this.kpis, this.map, this.list ]);

		if (window.ResizeObserver)
			new ResizeObserver(() => this.data && this.layout()).observe(this.map);

		let last = null;
		const tick = ts => {
			const dt = last == null ? 0 : Math.min(0.1, (ts - last) / 1000);
			last = ts;
			for (const f of this.flows) {
				f.off += f.dir * f.speed * dt;
				f.el.style.strokeDashoffset = f.off.toFixed(1);
			}
			requestAnimationFrame(tick);
		};
		requestAnimationFrame(tick);
	},

	// bits/s from two samples of a byte-counter pair; null for the first sample or across a counter reset
	rates(key, t, a, b) {
		const p = this.prev.get(key);
		this.prev.set(key, { t, a, b, seen: this.gen });
		if (!p || !(t > p.t) || a < p.a || b < p.b) return [ null, null ];
		return [ (a - p.a) * 8 / (t - p.t), (b - p.b) * 8 / (t - p.t) ];
	},

	update(data) {
		this.gen = (this.gen || 0) + 1;
		this.root.style.setProperty('--nm-card', surface(this.root.parentElement || document.body));

		if (data.error || !Array.isArray(data.nodes) || !data.nodes.length) {
			this.data = null;
			this.map.style.display = 'none';
			this.list.style.display = 'none';
			this.kpis.replaceChildren(E('span', { 'class': 'nm-m' }, data.error || _('No mesh data yet')));
			return;
		}
		this.map.style.display = '';
		this.list.style.display = '';

		const nodes = data.nodes.filter(n => n && n.online);
		const byMesh = new Map(), own = new Set(), hosts = new Map();
		nodes.forEach(n => {
			if (n.mesh_mac) byMesh.set(lc(n.mesh_mac), n);
			[ n.mesh_mac, n.lan_mac ].forEach(m => m && own.add(lc(m)));
			(n.hosts || []).forEach(h => hosts.set(lc(h.mac), h));
		});

		const base = nodes.find(n => n.role == 'router') || null;
		nodes.forEach(n => {
			n._parent = n === base ? null : ((n.uplink && byMesh.get(lc(n.uplink.mac))) || base);
			if (n._parent === n) n._parent = base === n ? null : base;
		});
		nodes.forEach(n => {
			let d = 1, p = n._parent, guard = 0;
			while (p && guard++ < 8) { d++; p = p._parent; }
			n._depth = d;
		});

		// live rates per node: the base shows its internet traffic, a satellite the traffic of its devices
		nodes.forEach(n => {
			if (n.wan)
				[ n._down, n._up ] = this.rates(`wan:${n.ip}`, n.t, n.wan.rx, n.wan.tx);
			else {
				let rx = 0, tx = 0;
				(n.radios || []).concat(n.ports || []).forEach(i => { rx += i.rx; tx += i.tx; });
				[ n._up, n._down ] = this.rates(`dev:${n.ip}`, n.t, rx, tx);
			}
			if (n.uplink)
				[ n._ldown, n._lup ] = this.rates(`link:${n.ip}:${lc(n.uplink.mac)}`, n.t, n.uplink.rx, n.uplink.tx);
		});

		// devices: Wi-Fi stations, then MACs on Ethernet jacks that are not already known as Wi-Fi or a node
		const devices = [], seen = new Set();
		nodes.forEach(n => (n.clients || []).forEach(c => {
			const mac = lc(c.mac);
			if (own.has(mac) || seen.has(mac)) return;
			seen.add(mac);
			const [ down, up ] = this.rates(`sta:${n.ip}:${mac}`, n.t, c.tx, c.rx);
			devices.push({ mac, node: n, band: c.band, signal: c.signal, phyDown: c.tx_rate, phyUp: c.rx_rate, down, up, online: c.connected });
		}));
		nodes.forEach(n => (n.wired || []).forEach(w => {
			const mac = lc(w.mac);
			if (own.has(mac) || seen.has(mac)) return;
			seen.add(mac);
			devices.push({ mac, node: n, band: `${_('Ethernet')} ${w.port}`, wired: true, idle: w.idle });
		}));
		devices.forEach(d => {
			const h = hosts.get(d.mac) || {};
			d.name = h.name || '';
			d.ip = h.ip || '';
			d.you = !!(this.viewer && d.ip && d.ip == this.viewer);
		});
		nodes.forEach(n => n._devices = devices.filter(d => d.node === n));

		for (const [ k, v ] of this.prev)
			if (v.seen < this.gen - 3) this.prev.delete(k);

		this.data = { self: data.self, nodes, base, devices, you: devices.find(d => d.you) };
		if (this.selected && !nodes.some(n => n.ip == this.selected)) this.selected = null;

		this.renderKpis();
		this.syncCards();
		this.layout();
		this.renderList();
	},

	renderKpis() {
		const { nodes, base, devices, you, self } = this.data;
		const me = nodes.find(n => n.ip == self);
		const k = (label, ...value) => E('div', { 'class': 'nm-kpi' }, [ E('span', { 'class': 'l' }, label), ...value ]);
		const parts = [];

		if (base && base.wan)
			parts.push(k(_('Internet'),
				E('b', { 'class': 'd' }, `↓ ${num(base._down)}`), E('b', { 'class': 'u' }, `↑ ${num(base._up)}`), E('span', { 'class': 'l' }, 'Mbit/s')));
		parts.push(k(_('Nodes'), E('b', {}, String(nodes.length))));
		parts.push(k(_('Devices'), E('b', {}, String(devices.length))));
		if (me)
			parts.push(k(_('This page'), E('b', {}, me.host)));
		if (you)
			parts.push(k(_('Your device'), E('b', {}, `${you.node.host}`), E('span', { 'class': 'l' }, you.band)));

		this.kpis.replaceChildren(...parts);
	},

	syncCards() {
		const { nodes } = this.data;
		const ips = new Set(nodes.map(n => n.ip));

		for (const [ ip, c ] of this.cards)
			if (!ips.has(ip)) { c.el.remove(); this.cards.delete(ip); }

		if (this.data.base && !this.inet) {
			this.inet = { el: E('div', { 'class': 'nm-inet' }), sub: E('div', { 'class': 'nm-sub' }) };
			const g = E('div', { 'class': 'nm-globe' });
			g.innerHTML = ICON_GLOBE;
			this.inet.el.append(g, E('div', { 'class': 'nm-name' }, _('Internet')), this.inet.sub);
			this.stage.appendChild(this.inet.el);
		}
		else if (!this.data.base && this.inet) {
			this.inet.el.remove();
			this.inet = null;
		}
		if (this.inet)
			this.inet.sub.textContent = this.data.base.wan && this.data.base.wan.up ? _('Connected') : _('Offline');

		nodes.forEach(n => {
			let c = this.cards.get(n.ip);
			if (!c) {
				c = { ico: E('div', { 'class': 'nm-ico' }), name: E('div', { 'class': 'nm-name' }), sub: E('div', { 'class': 'nm-sub' }),
				      dot: E('span', { 'class': 'nm-dot' }), d: E('span', { 'class': 'd' }), u: E('span', { 'class': 'u' }),
				      what: E('small', {}), foot1: E('span'), foot2: E('span'), tags: E('div', { 'class': 'nm-tags' }) };
				c.el = E('div', { 'class': 'nm-node', 'click': () => { this.selected = this.selected == n.ip ? null : n.ip; this.syncCards(); this.renderList(); } }, [
					c.tags,
					E('div', { 'class': 'nm-head' }, [ c.ico, E('div', { 'class': 'nm-t' }, [ c.name, c.sub ]), c.dot ]),
					E('div', { 'class': 'nm-rates' }, [ c.d, c.u, c.what ]),
					E('div', { 'class': 'nm-foot' }, [ c.foot1, c.foot2 ])
				]);
				this.cards.set(n.ip, c);
				this.stage.appendChild(c.el);
			}
			const state = LIGHT[(n.light || '').split(' ')[0]] || 'fair';
			if (c.state != state) { c.ico.innerHTML = ICON_NODE(LED[state]); c.state = state; }
			c.dot.className = `nm-dot ${state == 'ok' ? '' : state}`;
			c.el.title = [ n.status, `${_('up')} ${dur(n.uptime)}`, n.release ].filter(Boolean).join(' · ');
			c.name.textContent = n.host;
			// hops from the drawn chain, not the kernel's momentary mesh-path hop count, which can read low
			// for a moment while a path refreshes
			const hops = n._depth - 1;
			c.sub.textContent = n.role == 'router' ? _('Main node') : `${_('Satellite')} ${n.n} · ${hops} ${hops == 1 ? _('hop') : _('hops')}`;
			c.d.textContent = `↓ ${num(n._down)}`;
			c.u.textContent = `↑ ${num(n._up)}`;
			c.what.textContent = n.wan ? _('internet') : _('devices');
			const nd = n._devices.length;
			c.foot1.textContent = nd == 1 ? _('1 device') : `${nd} ${_('devices')}`;
			c.foot2.textContent = n.ip;
			c.el.classList.toggle('self', n.ip == this.data.self);
			c.el.classList.toggle('sel', n.ip == this.selected);

			const tags = [];
			if (n.ip == this.data.self) tags.push(E('span', { 'class': 'nm-tag', 'title': _('The LuCI you are looking at runs on this node') }, _('This node')));
			if (this.data.you && this.data.you.node === n) tags.push(E('span', { 'class': 'nm-tag you', 'title': _('Your device is connected to this node') }, _('You')));
			c.tags.replaceChildren(...tags);
		});
	},

	layout() {
		if (!this.data) return;
		const { nodes, base } = this.data;
		const maxDepth = Math.max(1, ...nodes.map(n => n._depth));

		// columns by hop count; inside a column, children follow their parent's order
		const cols = [];
		for (let d = 1; d <= maxDepth; d++) cols.push([]);
		const order = n => (n._parent ? order(n._parent) * 10 : 0) + (n.role == 'router' ? 0 : n.n);
		nodes.slice().sort((a, b) => order(a) - order(b)).forEach(n => cols[n._depth - 1].push(n));

		const inetW = base ? 128 : 0, avail = this.map.clientWidth || 0;
		const gap = Math.max(44, Math.min(150, (avail - inetW - maxDepth * CARD_W - 40) / maxDepth));
		const width = Math.max(avail, inetW + maxDepth * (CARD_W + gap) + 40);
		const height = Math.max(160, Math.max(...cols.map(c => c.length)) * ROW + 36);
		const x0 = inetW + CARD_W / 2 + gap / 2, x1 = width - CARD_W / 2 - 20;
		const colX = d => maxDepth == 1 ? (x0 + x1) / 2 : x0 + (x1 - x0) * (d - 1) / (maxDepth - 1);

		this.stage.style.width = `${width}px`;
		this.stage.style.height = `${height}px`;
		this.svg.setAttribute('width', width);
		this.svg.setAttribute('height', height);

		const pos = new Map();
		cols.forEach((col, i) => col.forEach((n, j) => {
			const x = colX(i + 1), y = height / 2 + (j - (col.length - 1) / 2) * ROW;
			pos.set(n.ip, { x, y });
			const c = this.cards.get(n.ip);
			c.el.style.left = `${x - CARD_W / 2}px`;
			c.el.style.top = `${y - CARD_H / 2}px`;
		}));

		const wanted = new Set();
		if (base && this.inet) {
			const b = pos.get(base.ip), ix = 62, iy = height / 2;
			this.inet.el.style.left = `${ix - 42}px`;
			this.inet.el.style.top = `${iy - 34}px`;
			wanted.add('inet');
			this.link('inet', [ ix + 24, iy - 9 ], [ b.x - CARD_W / 2, b.y ], {
				down: base._down, up: base._up, cls: base.wan.up ? 'ok' : 'bad',
				label: base.wan.up ? '<b>WAN</b>' : `<b style="color:${LED.bad}">${_('WAN down')}</b>`,
				title: `${_('Internet')} ↓ ${num(base._down)} / ↑ ${num(base._up)} Mbit/s`
			});
		}
		nodes.forEach(n => {
			if (!n._parent || !pos.has(n._parent.ip)) return;
			const p = pos.get(n._parent.ip), q = pos.get(n.ip), u = n.uplink || {};
			const qual = quality(u.signal);
			wanted.add(n.ip);
			this.link(n.ip, [ p.x + CARD_W / 2, p.y ], [ q.x - CARD_W / 2, q.y ], {
				down: n._ldown, up: n._lup, cls: qual.cls,
				label: u.signal ? `<b style="color:${LED[qual.cls]}">${qual.word}</b> ${u.signal} dBm` : _('mesh'),
				title: u.signal ? `${n._parent.host} → ${n.host}: ${u.signal} dBm, PHY ↓ ${u.rx_rate} / ↑ ${u.tx_rate} Mbit/s, ` +
					`${_('expected')} ${u.expected} Mbit/s, ${n._depth - 1} ${_('hop(s) to the base')}; ${_('now')} ↓ ${num(n._ldown)} / ↑ ${num(n._lup)} Mbit/s` : ''
			});
		});
		for (const [ key, l ] of this.links)
			if (!wanted.has(key)) { l.g.remove(); l.lbl.remove(); this.links.delete(key); }
		this.flows = [];
		for (const l of this.links.values()) this.flows.push(l.fd, l.fu);
	},

	// one link: a quality-coloured track, two dashed flows (down along it, up against it) and a label
	link(key, a, b, o) {
		const NS = 'http://www.w3.org/2000/svg';
		let l = this.links.get(key);
		if (!l) {
			const mk = (cls, attrs) => {
				const p = document.createElementNS(NS, 'path');
				p.setAttribute('class', cls);
				Object.keys(attrs).forEach(k => p.setAttribute(k, attrs[k]));
				return p;
			};
			l = { g: document.createElementNS(NS, 'g'), lbl: E('div', { 'class': 'nm-lbl' }) };
			l.track = mk('t', { 'fill': 'none', 'stroke-width': '2', 'stroke-linecap': 'round' });
			l.pd = mk('d', { 'fill': 'none', 'stroke-width': '3.4', 'stroke-linecap': 'round', 'stroke-dasharray': '0.1 13' });
			l.pu = mk('u', { 'fill': 'none', 'stroke-width': '3.4', 'stroke-linecap': 'round', 'stroke-dasharray': '0.1 13' });
			// colours through style: SVG presentation attributes resolve neither var() nor color-mix()
			l.pd.style.stroke = 'var(--nm-down)';
			l.pu.style.stroke = 'var(--nm-up)';
			l.g.append(l.track, l.pd, l.pu);
			this.svg.appendChild(l.g);
			this.stage.appendChild(l.lbl);
			l.fd = { el: l.pd, dir: -1, off: 0, speed: 0 };
			l.fu = { el: l.pu, dir: 1, off: 0, speed: 0 };
			this.links.set(key, l);
		}
		const curve = (dy) => {
			const mx = (a[0] + b[0]) / 2;
			return `M${a[0]},${a[1] + dy} C${mx},${a[1] + dy} ${mx},${b[1] + dy} ${b[0]},${b[1] + dy}`;
		};
		l.track.setAttribute('d', curve(0));
		l.track.style.stroke = `color-mix(in srgb, ${LED[o.cls]} 55%, transparent)`;
		l.pd.setAttribute('d', curve(-3.2));
		l.pu.setAttribute('d', curve(3.2));

		// speed and brightness follow the traffic on a log scale: a trickle crawls, 300 Mbit/s races
		[ [ l.fd, l.pd, o.down ], [ l.fu, l.pu, o.up ] ].forEach(([ f, el, bps ]) => {
			const mb = bps > 0 ? bps / 1e6 : 0, s = Math.min(1, Math.log10(1 + mb) / Math.log10(301));
			f.speed = mb > 0.005 ? 14 + 110 * s : 0;
			el.style.opacity = mb > 0.005 ? (0.45 + 0.55 * s).toFixed(2) : '0.14';
		});

		l.lbl.innerHTML = o.label;
		l.lbl.title = o.title || '';
		l.lbl.style.left = `${(a[0] + b[0]) / 2}px`;
		l.lbl.style.top = `${(a[1] + b[1]) / 2}px`;
	},

	renderList() {
		if (!this.data) return;
		const { nodes, devices } = this.data;
		const sel = this.selected;
		const chip = (label, ip) => E('button', {
			'class': `nm-chip${sel == ip ? ' on' : ''}`, 'type': 'button',
			'click': () => { this.selected = ip; this.syncCards(); this.renderList(); }
		}, label);

		this.filter.replaceChildren(E('span', { 'class': 'l' }, _('Connected devices')), chip(`${_('All')} ${devices.length}`, null),
			...nodes.map(n => chip(`${n.host} ${n._devices.length}`, n.ip)));

		const rows = devices.filter(d => !sel || d.node.ip == sel).sort((a, b) =>
			(b.you - a.you) || (((b.down || 0) + (b.up || 0)) - ((a.down || 0) + (a.up || 0))) || (a.name || a.mac).localeCompare(b.name || b.mac));

		if (!rows.length) {
			this.tbody.replaceChildren(E('tr', {}, E('td', { 'colspan': 6, 'class': 'nm-empty' }, _('No devices on this node right now'))));
			return;
		}

		this.tbody.replaceChildren(...rows.map(d => {
			const label = d.name || d.ip || d.mac;
			const hue = AVATAR[parseInt(d.mac.replace(/:/g, '').slice(-4), 16) % AVATAR.length];
			return E('tr', {}, [
				E('td', {}, E('div', { 'class': 'nm-dev' }, [
					E('span', { 'class': 'nm-av', 'style': `background:${hue}` }, (d.name || '?').charAt(0).toUpperCase()),
					E('div', {}, [ E('b', {}, label), d.you ? E('span', { 'class': 'nm-me' }, _('You')) : '',
						E('small', {}, [ d.ip, d.ip ? ' · ' : '', d.mac ].join('')) ])
				])),
				E('td', {}, [ d.node.host, E('span', { 'class': 'nm-band' }, d.band) ]),
				E('td', {}, d.wired ? E('span', { 'class': 'nm-m' }, '—') : [ bars(d.signal), `${d.signal} dBm` ]),
				E('td', {}, d.wired ? E('span', { 'class': 'nm-m' }, _('wired')) :
					[ E('span', { 'class': 'nm-d' }, `↓ ${d.phyDown}`), '  ', E('span', { 'class': 'nm-u' }, `↑ ${d.phyUp}`), E('span', { 'class': 'nm-m' }, ' Mbit/s') ]),
				E('td', {}, d.wired ? E('span', { 'class': 'nm-m' }, '—') :
					[ E('span', { 'class': 'nm-d' }, `↓ ${num(d.down)}`), '  ', E('span', { 'class': 'nm-u' }, `↑ ${num(d.up)}`), E('span', { 'class': 'nm-m' }, ' Mbit/s') ]),
				E('td', { 'class': 'nm-m' }, d.wired ? `${_('seen')} ${dur(d.idle)} ${_('ago')}` : dur(d.online))
			]);
		}));
	}
});
