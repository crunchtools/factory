#!/usr/bin/env python3
"""CrunchTools factory dashboard — serves game-style status page on port 8095.

Reads /data/factory-status.json (written by factory-watchdog) and renders
a Phaser 3 factory floor visualization showing software delivery health.

Live service monitoring is handled by Zabbix natively — this dashboard
focuses exclusively on software delivery: GHA, version sync, artifact
sync, constitution compliance, and open issues/PRs.

No pip dependencies — stdlib only.  Phaser 3 loaded from CDN.
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

STATUS_FILE = os.environ.get("STATUS_FILE", "/data/factory-status.json")
LISTEN_PORT = int(os.environ.get("DASHBOARD_PORT", "8095"))

GAME_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CrunchTools Software Factory</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { overflow: hidden; background: #0a0a1a; }
#loading {
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    color: #00d4ff;
    font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
    font-size: 16px;
    letter-spacing: 4px;
    animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 1; } }
</style>
</head>
<body>
<div id="loading">INITIALIZING FACTORY...</div>
<script src="https://cdn.jsdelivr.net/npm/phaser@3.80.1/dist/phaser.min.js"></script>
<script>

class FactoryScene extends Phaser.Scene {
    constructor() {
        super('Factory');
    }

    create() {
        document.getElementById('loading').style.display = 'none';

        this.factoryData = null;
        this.conveyors = [];
        this.gears = [];
        this.failingGlows = [];
        this.scanY = 0;
        this.isFirstLoad = true;

        this.generateTextures();

        this.gridGfx = this.add.graphics().setDepth(0);
        this.drawGrid();

        this.factoryContainer = this.add.container(0, 0).setDepth(1);
        this.hudContainer = this.add.container(0, 0).setDepth(10).setScrollFactor(0);
        this.scanGfx = this.add.graphics().setDepth(5).setScrollFactor(0);

        this.createHUD();
        this.createTooltip();
        this.createLegend();
        this.loadData();

        this.time.addEvent({
            delay: 60000,
            callback: function() { this.loadData(); },
            callbackScope: this,
            loop: true
        });

        this.input.on('wheel', function(pointer, gameObjects, deltaX, deltaY) {
            var cam = this.cameras.main;
            cam.scrollY = Math.max(0, Math.min(
                cam.scrollY + deltaY * 0.5,
                Math.max(0, (this.worldHeight || 0) - this.scale.height)
            ));
        }, this);
    }

    generateTextures() {
        var g, r, cx, cy, teeth, i, a;

        // Gear
        g = this.make.graphics();
        r = 10; cx = r + 3; cy = r + 3;
        g.lineStyle(1.5, 0xffffff, 1);
        g.strokeCircle(cx, cy, r * 0.35);
        g.fillStyle(0xffffff, 0.25);
        g.fillCircle(cx, cy, r * 0.18);
        teeth = 8;
        for (i = 0; i < teeth; i++) {
            a = (i / teeth) * Math.PI * 2;
            g.lineStyle(2, 0xffffff, 0.7);
            g.beginPath();
            g.moveTo(cx + Math.cos(a) * r * 0.45, cy + Math.sin(a) * r * 0.45);
            g.lineTo(cx + Math.cos(a) * r * 0.95, cy + Math.sin(a) * r * 0.95);
            g.strokePath();
        }
        g.generateTexture('gear', (r + 3) * 2, (r + 3) * 2);
        g.destroy();

        // Conveyor dash pattern
        g = this.make.graphics();
        g.lineStyle(1.5, 0x3a3a5a, 0.7);
        for (i = 0; i < 3; i++) {
            g.beginPath();
            g.moveTo(i * 10, 12);
            g.lineTo(i * 10 + 12, 0);
            g.strokePath();
        }
        g.generateTexture('conveyor', 30, 12);
        g.destroy();

        // Smoke particle
        g = this.make.graphics();
        g.fillStyle(0xffffff, 1);
        g.fillCircle(4, 4, 4);
        g.generateTexture('smoke', 8, 8);
        g.destroy();

        // Spark particle
        g = this.make.graphics();
        g.fillStyle(0xffffff, 1);
        g.fillCircle(2, 2, 2);
        g.generateTexture('spark', 4, 4);
        g.destroy();
    }

    drawGrid() {
        var g = this.gridGfx;
        g.clear();
        var w = this.scale.width;
        var h = Math.max(this.scale.height, this.worldHeight || 0);
        g.lineStyle(1, 0x151530, 0.4);
        for (var x = 0; x < w; x += 40) {
            g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.strokePath();
        }
        for (var y = 0; y < h; y += 40) {
            g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.strokePath();
        }
    }

    createHUD() {
        this.titleText = this.add.text(20, 15, 'CRUNCHTOOLS SOFTWARE FACTORY', {
            fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
            fontSize: '18px',
            color: '#00d4ff',
            fontStyle: 'bold'
        });
        this.hudContainer.add(this.titleText);

        // Subtle title glow
        this.tweens.add({
            targets: this.titleText,
            alpha: { from: 1, to: 0.7 },
            duration: 3000,
            ease: 'Sine.easeInOut',
            yoyo: true,
            repeat: -1
        });

        this.statusText = this.add.text(20, 42, 'Connecting to watchdog...', {
            fontFamily: '"Cascadia Code", "Fira Code", monospace',
            fontSize: '11px',
            color: '#666666'
        });
        this.hudContainer.add(this.statusText);

        this.healthGfx = this.add.graphics();
        this.hudContainer.add(this.healthGfx);

        this.healthText = this.add.text(0, 22, '', {
            fontFamily: '"Cascadia Code", monospace',
            fontSize: '13px',
            color: '#00ff88',
            fontStyle: 'bold'
        });
        this.hudContainer.add(this.healthText);

        // Divider
        this.dividerGfx = this.add.graphics();
        this.hudContainer.add(this.dividerGfx);
        this.drawDivider();
    }

    drawDivider() {
        this.dividerGfx.clear();
        this.dividerGfx.lineStyle(1, 0x0f3460, 0.8);
        this.dividerGfx.beginPath();
        this.dividerGfx.moveTo(0, 65);
        this.dividerGfx.lineTo(this.scale.width, 65);
        this.dividerGfx.strokePath();
    }

    createTooltip() {
        this.tooltip = this.add.container(0, 0).setDepth(100).setVisible(false).setScrollFactor(0);
        this.tooltipBg = this.add.graphics();
        this.tooltipTextObj = this.add.text(0, 0, '', {
            fontFamily: '"Cascadia Code", monospace',
            fontSize: '10px',
            color: '#e0e0e0',
            lineSpacing: 3,
            padding: { x: 10, y: 8 }
        });
        this.tooltip.add(this.tooltipBg);
        this.tooltip.add(this.tooltipTextObj);
    }

    showTooltip(worldX, worldY, repo) {
        var lines = [
            repo.name,
            '---',
            'GHA: ' + (repo.gha === 1 ? 'PASS' : repo.gha === 0 ? 'FAIL' : 'n/a'),
            'Version: ' + (repo.version || 'n/a') +
                (repo.version_sync === 1 ? ' (synced)' : repo.version_sync === 0 ? ' (MISMATCH)' : ''),
            'Artifacts: ' + (repo.artifact_sync === 1 ? 'synced' : repo.artifact_sync === 0 ? 'MISMATCH' : 'n/a'),
            'Constitution: ' + (repo.constitution === 1 ? 'PASS' : repo.constitution === 0 ? 'FAIL' : 'n/a'),
            'Issues: ' + (repo.issues_open || 0) + '  PRs: ' + (repo.prs_open || 0)
        ];
        this.tooltipTextObj.setText(lines.join('\\n'));

        var tw = this.tooltipTextObj.width + 20;
        var th = this.tooltipTextObj.height + 16;

        this.tooltipBg.clear();
        this.tooltipBg.fillStyle(0x0a0a2e, 0.95);
        this.tooltipBg.fillRoundedRect(-10, -8, tw, th, 6);
        this.tooltipBg.lineStyle(1, 0x00d4ff, 0.5);
        this.tooltipBg.strokeRoundedRect(-10, -8, tw, th, 6);

        var cam = this.cameras.main;
        var sx = worldX - cam.scrollX;
        var sy = worldY - cam.scrollY - th - 8;
        if (sy < 5) sy = worldY - cam.scrollY + 85;
        if (sx + tw > this.scale.width - 10) sx = this.scale.width - tw - 10;

        this.tooltip.setPosition(sx, sy);
        this.tooltip.setVisible(true);
    }

    hideTooltip() {
        this.tooltip.setVisible(false);
    }

    createLegend() {
        var y = this.scale.height - 28;
        this.legendContainer = this.add.container(0, 0).setDepth(10).setScrollFactor(0);

        var bg = this.add.graphics();
        bg.fillStyle(0x0a0a1a, 0.9);
        bg.fillRect(0, y - 5, this.scale.width, 35);
        bg.lineStyle(1, 0x0f3460, 0.5);
        bg.beginPath(); bg.moveTo(0, y - 5); bg.lineTo(this.scale.width, y - 5); bg.strokePath();
        this.legendContainer.add(bg);

        var items = [
            { label: 'G = GHA', color: '#888' },
            { label: 'V = Version Sync', color: '#888' },
            { label: 'A = Artifact Sync', color: '#888' },
            { label: 'C = Constitution', color: '#888' }
        ];

        var lx = 20;
        for (var i = 0; i < items.length; i++) {
            var t = this.add.text(lx, y + 2, items[i].label, {
                fontFamily: 'monospace', fontSize: '10px', color: items[i].color
            });
            this.legendContainer.add(t);
            lx += t.width + 24;
        }

        // Green/red dot legend
        var dotLegendX = lx + 20;
        var dotGreen = this.add.graphics();
        dotGreen.fillStyle(0x00ff88, 0.9);
        dotGreen.fillCircle(dotLegendX, y + 8, 4);
        this.legendContainer.add(dotGreen);
        var tOk = this.add.text(dotLegendX + 8, y + 2, 'PASS', {
            fontFamily: 'monospace', fontSize: '10px', color: '#00ff88'
        });
        this.legendContainer.add(tOk);

        var dotRed = this.add.graphics();
        dotRed.fillStyle(0xff4444, 0.9);
        dotRed.fillCircle(dotLegendX + 55, y + 8, 4);
        this.legendContainer.add(dotRed);
        var tFail = this.add.text(dotLegendX + 63, y + 2, 'FAIL', {
            fontFamily: 'monospace', fontSize: '10px', color: '#ff4444'
        });
        this.legendContainer.add(tFail);

        var dotGray = this.add.graphics();
        dotGray.fillStyle(0x333344, 0.7);
        dotGray.fillCircle(dotLegendX + 108, y + 8, 4);
        this.legendContainer.add(dotGray);
        var tNA = this.add.text(dotLegendX + 116, y + 2, 'N/A', {
            fontFamily: 'monospace', fontSize: '10px', color: '#555555'
        });
        this.legendContainer.add(tNA);
    }

    loadData() {
        var self = this;
        fetch('/api/status')
            .then(function(r) {
                if (!r.ok) throw new Error('No data');
                return r.json();
            })
            .then(function(data) {
                if (data && Object.keys(data).length > 0) {
                    self.factoryData = data;
                    self.buildFactory();
                    self.updateHUD();
                    if (!self.isFirstLoad) self.showRefreshFlash();
                    self.isFirstLoad = false;
                }
            })
            .catch(function() {
                self.statusText.setText('Waiting for factory-watchdog first run...');
                self.statusText.setColor('#ffaa00');
            });
    }

    showRefreshFlash() {
        var flash = this.add.rectangle(
            this.scale.width / 2, this.scale.height / 2,
            this.scale.width, this.scale.height,
            0x00d4ff, 0.04
        ).setDepth(50).setScrollFactor(0);
        this.tweens.add({
            targets: flash,
            alpha: 0,
            duration: 800,
            ease: 'Power2',
            onComplete: function() { flash.destroy(); }
        });
    }

    updateHUD() {
        var d = this.factoryData;
        if (!d) return;

        var s = d.summary || {};
        var health = s.health === 1;
        var total = s.repos_total || 0;
        var healthy = s.repos_healthy || 0;

        var ageStr = '?';
        if (d.timestamp) {
            var age = Math.floor((Date.now() - new Date(d.timestamp).getTime()) / 1000);
            if (age < 120) ageStr = age + 's ago';
            else if (age < 7200) ageStr = Math.floor(age / 60) + 'm ago';
            else ageStr = Math.floor(age / 3600) + 'h ago';
        }

        this.statusText.setText(
            healthy + '/' + total + ' repos healthy  |  Updated ' + ageStr +
            '  |  org: ' + (d.org || 'crunchtools')
        );
        this.statusText.setColor('#888888');

        var w = this.scale.width;
        this.healthGfx.clear();
        var hc = health ? 0x00ff88 : 0xff4444;
        this.healthGfx.fillStyle(hc, 0.12);
        this.healthGfx.fillCircle(w - 35, 30, 20);
        this.healthGfx.fillStyle(hc, 0.3);
        this.healthGfx.fillCircle(w - 35, 30, 13);
        this.healthGfx.fillStyle(hc, 0.9);
        this.healthGfx.fillCircle(w - 35, 30, 5);

        this.healthText.setText(health ? 'HEALTHY' : 'DEGRADED');
        this.healthText.setColor(health ? '#00ff88' : '#ff4444');
        this.healthText.setPosition(w - 60, 22);
        this.healthText.setOrigin(1, 0);

        if (!health) {
            var fails = [];
            if (s.gha_failing) fails.push('GHA:' + s.gha_failing);
            if (s.constitution_failing) fails.push('Con:' + s.constitution_failing);
            if (s.version_failing) fails.push('Ver:' + s.version_failing);
            if (s.artifact_failing) fails.push('Art:' + s.artifact_failing);
            if (!this.failDetailText) {
                this.failDetailText = this.add.text(0, 0, '', {
                    fontFamily: 'monospace', fontSize: '10px', color: '#ff6666'
                });
                this.hudContainer.add(this.failDetailText);
            }
            this.failDetailText.setText(fails.join('  '));
            this.failDetailText.setPosition(w - 60, 40);
            this.failDetailText.setOrigin(1, 0);
            this.failDetailText.setVisible(true);
        } else if (this.failDetailText) {
            this.failDetailText.setVisible(false);
        }
    }

    buildFactory() {
        this.factoryContainer.removeAll(true);
        this.conveyors = [];
        this.gears = [];
        this.failingGlows = [];

        var d = this.factoryData;
        if (!d || !d.repos) return;

        var byProfile = {};
        var profileOrder = [
            'MCP Server', 'Container Image', 'Web Application',
            'Claude Skill', 'Autonomous Agent', 'Unknown'
        ];

        var entries = Object.entries(d.repos);
        for (var ei = 0; ei < entries.length; ei++) {
            var name = entries[ei][0];
            var info = entries[ei][1];
            var profile = info.profile || 'Unknown';
            if (!byProfile[profile]) byProfile[profile] = [];
            byProfile[profile].push(Object.assign({ name: name }, info));
        }

        // Sort repos within each profile
        var profileKeys = Object.keys(byProfile);
        for (var pk = 0; pk < profileKeys.length; pk++) {
            byProfile[profileKeys[pk]].sort(function(a, b) {
                return a.name.localeCompare(b.name);
            });
        }

        var profiles = [];
        for (var po = 0; po < profileOrder.length; po++) {
            if (byProfile[profileOrder[po]]) profiles.push(profileOrder[po]);
        }

        var COLORS = {
            'MCP Server': 0x2266cc,
            'Container Image': 0xcc7722,
            'Web Application': 0x22aa66,
            'Claude Skill': 0x8833cc,
            'Autonomous Agent': 0xcc2266,
            'Unknown': 0x555555
        };
        var ICONS = {
            'MCP Server': 'MCP',
            'Container Image': 'IMG',
            'Web Application': 'WEB',
            'Claude Skill': 'SKL',
            'Autonomous Agent': 'AGT',
            'Unknown': '???'
        };

        var startY = 82;
        var lineSpacing = 135;
        var machW = 108;
        var machH = 78;
        var machGap = 10;
        var leftMargin = 195;
        var w = this.scale.width;
        var self = this;

        for (var pi = 0; pi < profiles.length; pi++) {
            var prof = profiles[pi];
            var y = startY + pi * lineSpacing;
            var repos = byProfile[prof];
            var color = COLORS[prof] || 0x555555;
            var icon = ICONS[prof] || '???';
            var colorHex = '#' + color.toString(16).padStart(6, '0');

            // Profile label
            var labelGfx = this.add.graphics();
            labelGfx.fillStyle(color, 0.1);
            labelGfx.fillRoundedRect(8, y + 3, 175, 26, 4);
            labelGfx.lineStyle(1, color, 0.35);
            labelGfx.strokeRoundedRect(8, y + 3, 175, 26, 4);
            this.factoryContainer.add(labelGfx);

            var iconText = this.add.text(16, y + 8, '[' + icon + ']', {
                fontFamily: 'monospace', fontSize: '12px', color: colorHex
            });
            this.factoryContainer.add(iconText);

            var labelText = this.add.text(62, y + 8, prof + ' (' + repos.length + ')', {
                fontFamily: '"Cascadia Code", monospace', fontSize: '11px', color: '#888888'
            });
            this.factoryContainer.add(labelText);

            // Conveyor belt
            var conveyorY = y + 38;
            var conveyorW = Math.max(
                w - leftMargin + 20,
                repos.length * (machW + machGap) + 30
            );

            // Belt background
            var cBg = this.add.graphics();
            cBg.fillStyle(0x10102a, 0.85);
            cBg.fillRect(leftMargin - 15, conveyorY - 6, conveyorW, machH + 24);
            // Rails
            cBg.lineStyle(2, 0x252550, 0.7);
            cBg.beginPath();
            cBg.moveTo(leftMargin - 15, conveyorY - 6);
            cBg.lineTo(leftMargin + conveyorW - 15, conveyorY - 6);
            cBg.strokePath();
            cBg.beginPath();
            cBg.moveTo(leftMargin - 15, conveyorY + machH + 18);
            cBg.lineTo(leftMargin + conveyorW - 15, conveyorY + machH + 18);
            cBg.strokePath();
            this.factoryContainer.add(cBg);

            // Animated conveyor treads (top)
            var ct = this.add.tileSprite(
                leftMargin - 15, conveyorY - 6,
                conveyorW, 10, 'conveyor'
            ).setOrigin(0, 0).setAlpha(0.4);
            this.factoryContainer.add(ct);
            this.conveyors.push(ct);

            // Animated conveyor treads (bottom)
            var cb = this.add.tileSprite(
                leftMargin - 15, conveyorY + machH + 8,
                conveyorW, 10, 'conveyor'
            ).setOrigin(0, 0).setAlpha(0.6);
            this.factoryContainer.add(cb);
            this.conveyors.push(cb);

            // Machines
            for (var ri = 0; ri < repos.length; ri++) {
                var mx = leftMargin + ri * (machW + machGap);
                var my = conveyorY + 2;
                this.createMachine(mx, my, machW, machH, repos[ri], color);
            }
        }

        // Track world height for scrolling
        this.worldHeight = startY + profiles.length * lineSpacing + 50;
        this.cameras.main.setBounds(0, 0, w, Math.max(this.worldHeight, this.scale.height));
        this.drawGrid();
    }

    createMachine(x, y, w, h, repo, profileColor) {
        var healthy = repo.healthy !== false;
        var glowColor = healthy ? 0x00ff88 : 0xff4444;
        var self = this;

        // Glow
        var glow = this.add.graphics();
        var ga = healthy ? 0.05 : 0.1;
        glow.fillStyle(glowColor, ga);
        glow.fillRoundedRect(x - 4, y - 4, w + 8, h + 8, 8);
        this.factoryContainer.add(glow);
        if (!healthy) {
            this.failingGlows.push({ gfx: glow, x: x, y: y, w: w, h: h, color: glowColor });
        }

        // Body
        var body = this.add.graphics();
        body.fillStyle(profileColor, 0.18);
        body.fillRoundedRect(x, y, w, h, 5);
        body.lineStyle(1.5, glowColor, 0.6);
        body.strokeRoundedRect(x, y, w, h, 5);
        // Accent line
        body.lineStyle(1, profileColor, 0.2);
        body.beginPath();
        body.moveTo(x + 6, y + 30);
        body.lineTo(x + w - 6, y + 30);
        body.strokePath();
        this.factoryContainer.add(body);

        // Name
        var shortName = repo.name
            .replace(/^mcp-/, '')
            .replace(/-crunchtools$/, '');
        if (shortName.length > 13) shortName = shortName.substring(0, 12) + '\\u2026';

        var nameText = this.add.text(x + w / 2, y + 8, shortName, {
            fontFamily: '"Cascadia Code", monospace',
            fontSize: '10px',
            color: '#e0e0e0',
            fontStyle: 'bold'
        }).setOrigin(0.5, 0);
        this.factoryContainer.add(nameText);

        // Version
        if (repo.version && /^\\d/.test(repo.version)) {
            var verColor = repo.version_sync === 1 ? '#00ff88' : '#ff6666';
            var verText = this.add.text(x + w / 2, y + 21, 'v' + repo.version, {
                fontFamily: 'monospace', fontSize: '8px', color: verColor
            }).setOrigin(0.5, 0);
            this.factoryContainer.add(verText);
        }

        // Quality gate lights
        var gates = [
            { label: 'G', value: repo.gha },
            { label: 'V', value: repo.version_sync },
            { label: 'A', value: repo.artifact_sync },
            { label: 'C', value: repo.constitution }
        ];
        var lightY = y + h - 22;
        var lightSpacing = 20;
        var lightStartX = x + (w - (gates.length - 1) * lightSpacing) / 2;

        for (var gi = 0; gi < gates.length; gi++) {
            var gate = gates[gi];
            var lx = lightStartX + gi * lightSpacing;
            var lg = this.add.graphics();

            if (gate.value === null || gate.value === undefined) {
                lg.fillStyle(0x333344, 0.4);
                lg.fillCircle(lx, lightY, 4);
            } else if (gate.value === 1) {
                lg.fillStyle(0x00ff88, 0.2);
                lg.fillCircle(lx, lightY, 7);
                lg.fillStyle(0x00ff88, 0.9);
                lg.fillCircle(lx, lightY, 3);
            } else {
                lg.fillStyle(0xff4444, 0.25);
                lg.fillCircle(lx, lightY, 7);
                lg.fillStyle(0xff4444, 0.9);
                lg.fillCircle(lx, lightY, 3);
            }
            this.factoryContainer.add(lg);

            var gl = this.add.text(lx, lightY + 10, gate.label, {
                fontFamily: 'monospace', fontSize: '7px', color: '#555555'
            }).setOrigin(0.5, 0);
            this.factoryContainer.add(gl);
        }

        // Gear
        var gear = this.add.image(x + w - 14, y + 14, 'gear');
        gear.setTint(profileColor);
        gear.setAlpha(healthy ? 0.7 : 0.25);
        this.factoryContainer.add(gear);
        this.gears.push({ image: gear, speed: healthy ? 0.3 : 0.03 });

        // Issues/PRs badge
        var issues = repo.issues_open || 0;
        var prs = repo.prs_open || 0;
        if (issues > 0 || prs > 0) {
            var parts = [];
            if (issues > 0) parts.push(issues + 'i');
            if (prs > 0) parts.push(prs + 'pr');
            var badge = this.add.text(x + w - 4, y + h - 6, parts.join('/'), {
                fontFamily: 'monospace', fontSize: '7px', color: '#ffaa00'
            }).setOrigin(1, 1);
            this.factoryContainer.add(badge);
        }

        // Smoke particles (healthy)
        if (healthy) {
            try {
                var smokeEmitter = this.add.particles(x + w / 2, y - 3, 'smoke', {
                    speed: { min: 6, max: 18 },
                    angle: { min: 255, max: 285 },
                    scale: { start: 0.2, end: 0.7 },
                    alpha: { start: 0.12, end: 0 },
                    tint: 0x8888aa,
                    lifespan: 2500,
                    frequency: 900,
                    quantity: 1
                });
                this.factoryContainer.add(smokeEmitter);
            } catch (e) { /* particles not supported */ }
        } else {
            // Red sparks (failing)
            try {
                var sparkEmitter = this.add.particles(x + w / 2, y + h / 2, 'spark', {
                    speed: { min: 15, max: 50 },
                    angle: { min: 0, max: 360 },
                    scale: { start: 0.7, end: 0 },
                    alpha: { start: 0.7, end: 0 },
                    tint: 0xff4444,
                    lifespan: 700,
                    frequency: 2500,
                    quantity: 2
                });
                this.factoryContainer.add(sparkEmitter);
            } catch (e) { /* particles not supported */ }
        }

        // Interactive hit zone for tooltip
        var hitZone = this.add.zone(x + w / 2, y + h / 2, w, h).setInteractive();
        this.factoryContainer.add(hitZone);

        hitZone.on('pointerover', function() {
            self.showTooltip(x, y, repo);
        });
        hitZone.on('pointerout', function() {
            self.hideTooltip();
        });
    }

    update(time, delta) {
        // Animate conveyors
        for (var ci = 0; ci < this.conveyors.length; ci++) {
            this.conveyors[ci].tilePositionX -= delta * 0.03;
        }

        // Rotate gears
        for (var gi = 0; gi < this.gears.length; gi++) {
            this.gears[gi].image.angle += this.gears[gi].speed * delta * 0.1;
        }

        // Pulse failing glows
        for (var fi = 0; fi < this.failingGlows.length; fi++) {
            var fg = this.failingGlows[fi];
            var pulse = 0.06 + Math.sin(time * 0.004) * 0.06;
            fg.gfx.clear();
            fg.gfx.fillStyle(fg.color, pulse);
            fg.gfx.fillRoundedRect(fg.x - 4, fg.y - 4, fg.w + 8, fg.h + 8, 8);
        }

        // Scanning line
        this.scanY = (this.scanY + delta * 0.02) % this.scale.height;
        this.scanGfx.clear();
        this.scanGfx.lineStyle(1, 0x00d4ff, 0.03);
        this.scanGfx.beginPath();
        this.scanGfx.moveTo(0, this.scanY);
        this.scanGfx.lineTo(this.scale.width, this.scanY);
        this.scanGfx.strokePath();
    }
}

var config = {
    type: Phaser.AUTO,
    scale: {
        mode: Phaser.Scale.RESIZE,
        autoCenter: Phaser.Scale.NO_CENTER,
        width: window.innerWidth,
        height: window.innerHeight
    },
    backgroundColor: '#0a0a1a',
    scene: FactoryScene,
    disableContextMenu: true,
    banner: false
};

new Phaser.Game(config);

</script>
</body>
</html>
"""


def load_status() -> dict | None:
    """Load the status JSON file."""
    path = Path(STATUS_FILE)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the factory dashboard."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return

        if self.path == "/api/status":
            data = load_status()
            self.send_response(200 if data else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data or {}).encode())
            return

        # Default: serve game dashboard
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(GAME_HTML.encode())

    def log_message(self, format, *args):
        print(f"{self.address_string()} {args[0]}", flush=True)


def main() -> int:
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), DashboardHandler)
    print(f"Factory dashboard listening on port {LISTEN_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
