'use strict';
'require view';
'require form';
'require dom';
'require fs';
'require poll';
'require ui';
'require uci';

// System -> Firmware OTA: a front end for /usr/sbin/neon-ota (status, check, install) plus its
// /etc/config/neon_ota settings. All verification happens in neon-ota; this page only displays it.
// No String.prototype.format here: LuCI 26 moved it to cbi.js (see luci-theme-aurora/VENDORED.md).

function ota(args) {
	return fs.exec('/usr/sbin/neon-ota', args).then(function(res) {
		try {
			return JSON.parse(res.stdout);
		}
		catch (e) {
			return { result: 'error', message: ((res.stderr || '') + (res.stdout || '')).trim() || _('neon-ota returned no output') };
		}
	}, function(err) {
		return { result: 'error', message: err.message };
	});
}

function when(epoch) {
	return epoch > 0 ? new Date(epoch * 1000).toLocaleString() : _('not since boot');
}

return view.extend({
	load: function() {
		return Promise.all([ ota([ 'status', '--json' ]), uci.load('neon_ota') ]);
	},

	statusTable: function(st, chk) {
		var lu = st.last_upgrade || {},
		    rows = [
			[ _('Running release'), (st.running || _('unknown')) + ' (' + (st.profile || '?') + ')' ],
			[ _('Release server'), (st.base_url || '-') + ', ' + _('channel') + ' ' + (st.channel || '-') ],
			[ _('Install slot'), (st.role || '?') + ', ' + (st.slot || '?') + (st.auto_install ? '' : ' ' + _('(automatic install is off)')) ],
			[ _('Last check'), when(st.last_check) + (st.last_result ? ' → ' + st.last_result : '') ],
			[ _('Last OTA upgrade'), lu.to ? lu.from + ' → ' + lu.to + ', ' + when(lu.at) + ' (' + lu.by + '): ' + lu.result : _('none') ]
		];

		if (st.progress)
			rows.push([ _('Install activity'), st.progress + (st.progress_message ? ': ' + st.progress_message : '') ]);

		if (chk)
			rows.push([ _('Update'), E('strong', {}, chk.message || chk.result) ]);

		return E('table', { 'class': 'table' }, rows.map(function(r) {
			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'width': '33%' }, r[0]),
				E('td', { 'class': 'td left' }, r[1])
			]);
		}));
	},

	update: function(st, chk) {
		if (chk)
			this.chk = chk;

		dom.content(this.statusNode, this.statusTable(st, this.chk));

		var ready = this.chk && this.chk.result === 'update' && !st.flashing;
		this.installBtn.disabled = !ready;
		this.installBtn.textContent = ready ? _('Install') + ' ' + this.chk.available : _('Install update');
		this.st = st;
	},

	handleCheck: function() {
		return ota([ 'check', '--json' ]).then(L.bind(function(chk) {
			return ota([ 'status', '--json' ]).then(L.bind(function(st) {
				this.update(st, chk);
			}, this));
		}, this));
	},

	handleInstall: function() {
		var c = this.chk;

		ui.showModal(_('Install firmware'), [
			E('p', {}, _('Install') + ' ' + c.available + ' ' + _('over') + ' ' + (this.st.running || _('the running release')) + '?'),
			E('p', {}, _('The image is downloaded and verified first (signed manifest, sha256, sysupgrade -T); settings are kept. The unit then reboots and is unreachable for about three minutes: on the router that takes the internet down, on a satellite its clients and any unit meshed through it.')),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'cbi-button', 'click': ui.hideModal }, _('Cancel')),
				' ',
				E('button', { 'class': 'cbi-button cbi-button-negative important', 'click': ui.createHandlerFn(this, 'startInstall') }, _('Install'))
			])
		]);
	},

	startInstall: function() {
		var self = this, lost = 0, flashing = false;

		ui.showModal(_('Installing firmware'), [ E('p', { 'class': 'spinning' }, _('Downloading and verifying the image…')) ]);

		var waitForUnit = function() {
			flashing = true;
			poll.remove(tick);
			ui.showModal(_('Flashing…'), [
				E('p', { 'class': 'spinning' }, _('Writing the new firmware. Do not power off the unit; this page reconnects when it is back.'))
			]);
			window.setTimeout(function() { ui.awaitReconnect(); }, 90000);
		};

		var tick = function() {
			return ota([ 'status', '--json' ]).then(function(st) {
				if (flashing)
					return;

				if (!st.running) {		// rpcd is gone: sysupgrade has taken the unit down
					if (++lost >= 2)
						waitForUnit();
					return;
				}

				lost = 0;

				if (st.flashing || st.progress === 'flashing')
					return waitForUnit();

				if (st.progress === 'error' || st.progress === 'current') {
					poll.remove(tick);
					ui.hideModal();
					ui.addNotification(null, E('p', {}, st.progress_message), st.progress === 'error' ? 'danger' : 'info');
					self.update(st);
				}
			});
		};

		return ota([ 'upgrade', '--yes', '--detach', '--json' ]).then(function(res) {
			if (res.result !== 'started') {
				ui.hideModal();
				ui.addNotification(null, E('p', {}, res.message), 'danger');
				return;
			}
			poll.add(tick, 3);
		});
	},

	render: function(data) {
		var st = data[0], m, s, o;

		this.st = st;
		this.statusNode = E('div', {}, this.statusTable(st, null));
		this.installBtn = E('button', {
			'class': 'cbi-button cbi-button-apply',
			'disabled': true,
			'click': ui.createHandlerFn(this, 'handleInstall')
		}, _('Install update'));

		m = new form.Map('neon_ota');

		s = m.section(form.NamedSection, 'main', 'ota', _('Settings'));

		o = s.option(form.Flag, 'enabled', _('Check for updates'),
			_('Every few hours. A newer release is logged and shown here.'));
		o.rmempty = false;

		o = s.option(form.Flag, 'auto_install', _('Install automatically'),
			_('Unattended install inside this unit\'s slot: satellite N at window start + (N-1) × slot length, the router one slot after the last satellite.'));
		o.rmempty = false;
		o.depends('enabled', '1');

		o = s.option(form.Value, 'window_start', _('Window start'), _('Local time, HH:MM.'));
		o.placeholder = '03:00';
		o.validate = function(section_id, value) {
			return /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(value) ? true : _('Expecting HH:MM');
		};

		o = s.option(form.Value, 'slot_minutes', _('Slot length'), _('Minutes.'));
		o.placeholder = '20';
		o.datatype = 'range(10,120)';

		o = s.option(form.Value, 'check_hours', _('Check interval'), _('Hours.'));
		o.placeholder = '6';
		o.datatype = 'range(1,168)';

		o = s.option(form.Value, 'channel', _('Channel'));
		o.placeholder = 'stable';
		o.validate = function(section_id, value) {
			return /^[A-Za-z0-9_-]+$/.test(value) ? true : _('Letters, digits, - and _ only');
		};

		o = s.option(form.Value, 'base_url', _('Release server'),
			_('Manifests are read from &lt;release server&gt;/ota/&lt;channel&gt;.json and must be signed by this unit\'s build key.'));
		o.validate = function(section_id, value) {
			return /^https?:\/\/[^\s]+$/.test(value) ? true : _('Expecting an http(s) URL');
		};

		return m.render().then(L.bind(function(mapEl) {
			return E([], [
				E('h2', {}, _('Firmware OTA')),
				E('div', { 'class': 'cbi-map-descr' },
					_('Over-the-air firmware updates from the neon-mesh release server. Only a newer release than the running one is ever installed, and the configuration is kept.')),
				E('div', { 'class': 'cbi-section' }, [
					E('h3', {}, _('Status')),
					this.statusNode,
					E('div', { 'class': 'right' }, [
						E('button', { 'class': 'cbi-button cbi-button-action', 'click': ui.createHandlerFn(this, 'handleCheck') }, _('Check now')),
						' ',
						this.installBtn
					])
				]),
				mapEl
			]);
		}, this));
	}
});
