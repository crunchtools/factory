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
    font-size: 16px; letter-spacing: 4px;
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
    constructor() { super('Factory'); }

    create() {
        document.getElementById('loading').style.display = 'none';
        this.factoryData = null;
        this.conveyors = [];
        this.productionLines = [];
        this.activeFilter = 'ALL';
        this.soundEnabled = false;
        this.audioCtx = null;
        this.isFirstLoad = true;
        this.animGeneration = 0;
        this.scanY = 0;

        this.generateTextures();
        this.gridGfx = this.add.graphics().setDepth(0);
        this.drawGrid();
        this.factoryContainer = this.add.container(0, 0).setDepth(1);
        this.hudContainer = this.add.container(0, 0).setDepth(10).setScrollFactor(0);
        this.scanGfx = this.add.graphics().setDepth(5).setScrollFactor(0);

        this.createHUD();
        this.createTooltip();
        this.createFilterBar();
        this.createLegend();
        this.createSoundToggle();
        this.loadData();

        this.time.addEvent({
            delay: 60000,
            callback: function() { this.loadData(); },
            callbackScope: this,
            loop: true
        });

        var self = this;
        this.input.on('wheel', function(pointer, gameObjects, deltaX, deltaY) {
            var cam = self.cameras.main;
            cam.scrollY = Math.max(0, Math.min(
                cam.scrollY + deltaY * 0.5,
                Math.max(0, (self.worldHeight || 0) - self.scale.height)
            ));
        });
    }

    generateTextures() {
        var g;
        g = this.make.graphics();
        g.lineStyle(2, 0x5a5a8a, 0.9);
        for (var i = 0; i < 3; i++) {
            g.beginPath(); g.moveTo(i * 10, 12); g.lineTo(i * 10 + 12, 0); g.strokePath();
        }
        g.generateTexture('conveyor', 30, 12); g.destroy();

        g = this.make.graphics();
        g.fillStyle(0xffffff, 1); g.fillCircle(4, 4, 4);
        g.generateTexture('smoke', 8, 8); g.destroy();

        g = this.make.graphics();
        g.fillStyle(0xffffff, 1); g.fillCircle(2, 2, 2);
        g.generateTexture('spark', 4, 4); g.destroy();
    }

    drawGrid() {
        var g = this.gridGfx; g.clear();
        var w = this.scale.width, h = Math.max(this.scale.height, this.worldHeight || 0);
        g.lineStyle(1, 0x151530, 0.4);
        for (var x = 0; x < w; x += 40) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.strokePath(); }
        for (var y = 0; y < h; y += 40) { g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.strokePath(); }
    }

    createHUD() {
        this.titleText = this.add.text(20, 15, 'CRUNCHTOOLS SOFTWARE FACTORY', {
            fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
            fontSize: '18px', color: '#00d4ff', fontStyle: 'bold'
        });
        this.hudContainer.add(this.titleText);
        this.tweens.add({
            targets: this.titleText, alpha: { from: 1, to: 0.7 },
            duration: 3000, ease: 'Sine.easeInOut', yoyo: true, repeat: -1
        });
        this.statusText = this.add.text(20, 42, 'Connecting to watchdog...', {
            fontFamily: '"Cascadia Code", "Fira Code", monospace', fontSize: '11px', color: '#666666'
        });
        this.hudContainer.add(this.statusText);
        this.healthGfx = this.add.graphics();
        this.hudContainer.add(this.healthGfx);
        this.healthText = this.add.text(0, 22, '', {
            fontFamily: '"Cascadia Code", monospace', fontSize: '13px', color: '#00ff88', fontStyle: 'bold'
        });
        this.hudContainer.add(this.healthText);
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
            fontFamily: '"Cascadia Code", monospace', fontSize: '10px',
            color: '#e0e0e0', lineSpacing: 3, padding: { x: 10, y: 8 }
        });
        this.tooltip.add(this.tooltipBg);
        this.tooltip.add(this.tooltipTextObj);
    }

    showTooltip(worldX, worldY, repo) {
        var lines = [
            repo.name, '---',
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
        if (sy < 5) sy = worldY - cam.scrollY + 30;
        if (sx + tw > this.scale.width - 10) sx = this.scale.width - tw - 10;
        this.tooltip.setPosition(sx, sy);
        this.tooltip.setVisible(true);
    }

    hideTooltip() { this.tooltip.setVisible(false); }

    createFilterBar() {
        this.filterContainer = this.add.container(0, 0).setDepth(10).setScrollFactor(0);
        var filters = [
            { key: 'ALL', label: 'ALL' }, { key: 'FAILING', label: 'FAILING' },
            { key: 'MCP', label: 'MCP' }, { key: 'IMG', label: 'IMG' },
            { key: 'WEB', label: 'WEB' }, { key: 'SKL', label: 'SKL' },
            { key: 'AGT', label: 'AGT' }
        ];
        var x = 20, self = this;
        this.filterBtns = {};
        for (var i = 0; i < filters.length; i++) {
            var f = filters[i];
            var active = f.key === this.activeFilter;
            var btn = this.add.text(x, 72, f.label, {
                fontFamily: 'monospace', fontSize: '10px',
                color: active ? '#00d4ff' : '#555555',
                padding: { x: 6, y: 3 }
            }).setInteractive({ useHandCursor: true });
            btn.on('pointerup', (function(k) {
                return function() { self.setFilter(k); };
            })(f.key));
            this.filterContainer.add(btn);
            this.filterBtns[f.key] = btn;
            x += btn.width + 8;
        }
    }

    setFilter(key) {
        this.activeFilter = key;
        var keys = Object.keys(this.filterBtns);
        for (var i = 0; i < keys.length; i++) {
            this.filterBtns[keys[i]].setColor(keys[i] === key ? '#00d4ff' : '#555555');
        }
        if (this.factoryData) this.buildFactory();
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
            { l: 'GHA = GitHub Actions', c: '#888' },
            { l: 'VER = Version Sync', c: '#888' },
            { l: 'ART = Artifact Sync', c: '#888' },
            { l: 'CON = Constitution', c: '#888' }
        ];
        var lx = 20;
        for (var i = 0; i < items.length; i++) {
            var t = this.add.text(lx, y + 2, items[i].l, {
                fontFamily: 'monospace', fontSize: '10px', color: items[i].c
            });
            this.legendContainer.add(t);
            lx += t.width + 24;
        }
        var dx = lx + 20, dg;
        dg = this.add.graphics(); dg.fillStyle(0x00ff88, 0.9); dg.fillCircle(dx, y + 8, 4);
        this.legendContainer.add(dg);
        this.legendContainer.add(this.add.text(dx + 8, y + 2, 'PASS', {
            fontFamily: 'monospace', fontSize: '10px', color: '#00ff88'
        }));
        dg = this.add.graphics(); dg.fillStyle(0xff4444, 0.9); dg.fillCircle(dx + 55, y + 8, 4);
        this.legendContainer.add(dg);
        this.legendContainer.add(this.add.text(dx + 63, y + 2, 'FAIL', {
            fontFamily: 'monospace', fontSize: '10px', color: '#ff4444'
        }));
        dg = this.add.graphics(); dg.fillStyle(0x333344, 0.7); dg.fillCircle(dx + 108, y + 8, 4);
        this.legendContainer.add(dg);
        this.legendContainer.add(this.add.text(dx + 116, y + 2, 'N/A', {
            fontFamily: 'monospace', fontSize: '10px', color: '#555555'
        }));
    }

    createSoundToggle() {
        this.soundBtn = this.add.text(this.scale.width - 100, 52, '[MUTE]', {
            fontFamily: 'monospace', fontSize: '9px', color: '#555555',
            padding: { x: 4, y: 2 }
        }).setInteractive({ useHandCursor: true });
        this.hudContainer.add(this.soundBtn);
        var self = this;
        this.soundBtn.on('pointerup', function() {
            self.soundEnabled = !self.soundEnabled;
            if (self.soundEnabled) {
                self.initAudio();
                self.soundBtn.setText('[SND]');
                self.soundBtn.setColor('#00d4ff');
            } else {
                self.soundBtn.setText('[MUTE]');
                self.soundBtn.setColor('#555555');
            }
        });
    }

    initAudio() {
        if (!this.audioCtx) {
            try { this.audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
            catch (e) { /* no audio support */ }
        }
    }

    playSound(type) {
        if (!this.soundEnabled || !this.audioCtx) return;
        var ctx = this.audioCtx, now = ctx.currentTime;
        var osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        if (type === 'clunk') {
            osc.type = 'square'; osc.frequency.value = 80;
            gain.gain.setValueAtTime(0.12, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
            osc.start(now); osc.stop(now + 0.08);
        } else if (type === 'ding') {
            osc.type = 'sine'; osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.08, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
            osc.start(now); osc.stop(now + 0.25);
        } else if (type === 'buzz') {
            osc.type = 'sawtooth'; osc.frequency.value = 120;
            gain.gain.setValueAtTime(0.12, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
            osc.start(now); osc.stop(now + 0.15);
        } else if (type === 'chime') {
            osc.type = 'sine';
            osc.frequency.setValueAtTime(523, now);
            osc.frequency.setValueAtTime(659, now + 0.1);
            osc.frequency.setValueAtTime(784, now + 0.2);
            gain.gain.setValueAtTime(0.08, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);
            osc.start(now); osc.stop(now + 0.4);
        }
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
            this.scale.width, this.scale.height, 0x00d4ff, 0.04
        ).setDepth(50).setScrollFactor(0);
        this.tweens.add({
            targets: flash, alpha: 0, duration: 800, ease: 'Power2',
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
        var hc = health ? 0x00ff88 : 0xff4444;
        this.healthGfx.clear();
        this.healthGfx.fillStyle(hc, 0.12); this.healthGfx.fillCircle(w - 35, 30, 20);
        this.healthGfx.fillStyle(hc, 0.3); this.healthGfx.fillCircle(w - 35, 30, 13);
        this.healthGfx.fillStyle(hc, 0.9); this.healthGfx.fillCircle(w - 35, 30, 5);
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
        this.animGeneration++;
        this.factoryContainer.removeAll(true);
        this.conveyors = [];
        this.productionLines = [];

        var d = this.factoryData;
        if (!d || !d.repos) return;

        var byProfile = {};
        var profileOrder = [
            'MCP Server', 'Container Image', 'Web Application',
            'Claude Skill', 'Autonomous Agent', 'Unknown'
        ];
        var entries = Object.entries(d.repos);
        for (var ei = 0; ei < entries.length; ei++) {
            var name = entries[ei][0], info = entries[ei][1];
            var profile = info.profile || 'Unknown';
            if (!byProfile[profile]) byProfile[profile] = [];
            byProfile[profile].push(Object.assign({ name: name }, info));
        }
        var pk = Object.keys(byProfile);
        for (var i = 0; i < pk.length; i++) {
            byProfile[pk[i]].sort(function(a, b) { return a.name.localeCompare(b.name); });
        }
        var profiles = [];
        for (var po = 0; po < profileOrder.length; po++) {
            if (byProfile[profileOrder[po]]) profiles.push(profileOrder[po]);
        }

        var filterMap = {
            'MCP': 'MCP Server', 'IMG': 'Container Image',
            'WEB': 'Web Application', 'SKL': 'Claude Skill', 'AGT': 'Autonomous Agent'
        };
        if (this.activeFilter === 'FAILING') {
            profiles = profiles.filter(function(p) {
                return byProfile[p].some(function(r) { return !r.healthy; });
            });
        } else if (filterMap[this.activeFilter]) {
            var target = filterMap[this.activeFilter];
            profiles = profiles.filter(function(p) { return p === target; });
        }

        var COLORS = {
            'MCP Server': 0x2266cc, 'Container Image': 0xcc7722,
            'Web Application': 0x22aa66, 'Claude Skill': 0x8833cc,
            'Autonomous Agent': 0xcc2266, 'Unknown': 0x555555
        };
        var ICONS = {
            'MCP Server': 'MCP', 'Container Image': 'IMG',
            'Web Application': 'WEB', 'Claude Skill': 'SKL',
            'Autonomous Agent': 'AGT', 'Unknown': '???'
        };

        var startY = 95, lineSpacing = 140, w = this.scale.width;
        for (var pi = 0; pi < profiles.length; pi++) {
            var prof = profiles[pi];
            this.createProductionLine(
                prof, byProfile[prof], COLORS[prof] || 0x555555,
                ICONS[prof] || '???', startY + pi * lineSpacing, w,
                d.org || 'crunchtools'
            );
        }

        this.worldHeight = startY + profiles.length * lineSpacing + 40;
        this.cameras.main.setBounds(0, 0, w, Math.max(this.worldHeight, this.scale.height));
        this.drawGrid();
        this.animatePackages();
    }

    createProductionLine(profile, repos, color, icon, lineY, w, org) {
        var labelText = this.add.text(16, lineY + 4,
            '[' + icon + '] ' + profile + ' (' + repos.length + ')', {
            fontFamily: '"Cascadia Code", monospace', fontSize: '11px', color: '#888888'
        });
        var boxW = labelText.width + 16;
        var labelBg = this.add.graphics();
        labelBg.fillStyle(color, 0.1);
        labelBg.fillRoundedRect(8, lineY, boxW, 22, 4);
        labelBg.lineStyle(1, color, 0.35);
        labelBg.strokeRoundedRect(8, lineY, boxW, 22, 4);
        this.factoryContainer.add(labelBg);
        this.factoryContainer.add(labelText);

        var bL = 15, bR = w - 15, bT = lineY + 28, bB = lineY + 120;
        var dockS = w * 0.82;

        var bBg = this.add.graphics();
        bBg.fillStyle(0x10102a, 0.85);
        bBg.fillRect(bL, bT, bR - bL, bB - bT);
        bBg.lineStyle(2, 0x252550, 0.7);
        bBg.beginPath(); bBg.moveTo(bL, bT); bBg.lineTo(bR, bT); bBg.strokePath();
        bBg.beginPath(); bBg.moveTo(bL, bB); bBg.lineTo(bR, bB); bBg.strokePath();
        this.factoryContainer.add(bBg);

        var ctT = this.add.tileSprite(bL, bT, bR - bL, 10, 'conveyor').setOrigin(0, 0).setAlpha(0.5);
        var ctB = this.add.tileSprite(bL, bB - 10, bR - bL, 10, 'conveyor').setOrigin(0, 0).setAlpha(0.7);
        this.factoryContainer.add(ctT);
        this.factoryContainer.add(ctB);
        this.conveyors.push(ctT, ctB);

        var dG = this.add.graphics();
        dG.fillStyle(0x00ff88, 0.04);
        dG.fillRect(dockS, bT + 1, bR - dockS - 1, bB - bT - 2);
        dG.lineStyle(1, 0x00ff88, 0.2);
        dG.strokeRect(dockS, bT + 1, bR - dockS - 1, bB - bT - 2);
        this.factoryContainer.add(dG);

        var shipped = repos.filter(function(r) { return r.healthy; }).length;
        var dLabel = this.add.text((dockS + bR) / 2, bB + 3,
            shipped + '/' + repos.length + ' shipped', {
            fontFamily: 'monospace', fontSize: '9px',
            color: shipped === repos.length ? '#00ff88' : '#ffaa00'
        }).setOrigin(0.5, 0);
        this.factoryContainer.add(dLabel);

        var gateArea = dockS - bL - 80;
        var gateStart = bL + 80;
        var gateSpacing = gateArea / 4;
        var gateKeys = ['gha', 'version_sync', 'artifact_sync', 'constitution'];
        var gateLabels = ['GHA', 'VER', 'ART', 'CON'];
        var gateXPositions = [];
        for (var gi = 0; gi < 4; gi++) {
            var gx = gateStart + gi * gateSpacing;
            gateXPositions.push(gx);
            this.createGateStructure(gx, lineY, gateKeys[gi], gateLabels[gi], repos, org);
        }

        try {
            var se = this.add.particles(bL + 25, lineY + 70, 'smoke', {
                speed: { min: 8, max: 20 }, angle: { min: 255, max: 285 },
                scale: { start: 0.3, end: 0.8 }, alpha: { start: 0.24, end: 0 },
                tint: 0x8888aa, lifespan: 2000, frequency: 450, quantity: 1
            });
            this.factoryContainer.add(se);
        } catch (e) { /* particles not supported */ }

        var pkgs = [];
        var dockW = bR - dockS - 30;
        var dockSpc = Math.min(50, dockW / Math.max(1, shipped));
        var si = 0;
        for (var ri = 0; ri < repos.length; ri++) {
            var repo = repos[ri];
            var dX = repo.healthy ? (dockS + 15 + si * dockSpc) : 0;
            if (repo.healthy) si++;
            var pkg = this.createPackage(repo, color, bL - 50, lineY, org);
            pkgs.push({ container: pkg, repo: repo, dockX: dX });
        }

        this.productionLines.push({
            repos: repos, gateXPositions: gateXPositions,
            lineY: lineY, packages: pkgs, org: org
        });
    }

    createGateStructure(gateX, lineY, gateKey, label, repos, org) {
        var pW = 8, gapH = 25, pTop = lineY + 35, pH = 70;
        var anyFail = false, anyPass = false;
        for (var i = 0; i < repos.length; i++) {
            var v = repos[i][gateKey];
            if (v === 0) anyFail = true;
            if (v === 1) anyPass = true;
        }
        var lc = anyFail ? 0xff4444 : anyPass ? 0x00ff88 : 0x333344;
        var la = (anyFail || anyPass) ? 0.9 : 0.4;
        var pc = anyFail ? 0x442222 : anyPass ? 0x224422 : 0x222233;

        var g = this.add.graphics();
        g.fillStyle(pc, 0.8);
        g.fillRect(gateX - gapH - pW, pTop, pW, pH);
        g.fillRect(gateX + gapH, pTop, pW, pH);
        g.lineStyle(1, lc, 0.3);
        g.strokeRect(gateX - gapH - pW, pTop, pW, pH);
        g.strokeRect(gateX + gapH, pTop, pW, pH);
        g.fillStyle(pc, 0.6);
        g.fillRect(gateX - gapH - pW, pTop, gapH * 2 + pW * 2, 4);
        g.fillStyle(lc, 0.15); g.fillCircle(gateX, lineY + 28, 10);
        g.fillStyle(lc, la); g.fillCircle(gateX, lineY + 28, 5);
        this.factoryContainer.add(g);

        var labelBg = this.add.graphics();
        var fullLabels = { 'GHA': 'GitHub Actions', 'VER': 'Version Sync', 'ART': 'Artifact Sync', 'CON': 'Constitution' };
        var fullLabel = fullLabels[label] || label;
        var lt = this.add.text(gateX, lineY + 26, label, {
            fontFamily: '"Cascadia Code", monospace', fontSize: '11px', color: '#cccccc', fontStyle: 'bold'
        }).setOrigin(0.5, 0.5);
        var lbW = lt.width + 12, lbH = lt.height + 6;
        labelBg.fillStyle(0x0a0a2e, 0.85);
        labelBg.fillRoundedRect(gateX - lbW / 2, lineY + 26 - lbH / 2, lbW, lbH, 3);
        labelBg.lineStyle(1, lc, 0.4);
        labelBg.strokeRoundedRect(gateX - lbW / 2, lineY + 26 - lbH / 2, lbW, lbH, 3);
        this.factoryContainer.add(labelBg);
        this.factoryContainer.add(lt);
        this.factoryContainer.add(this.add.text(gateX, lineY + 113, fullLabel, {
            fontFamily: 'monospace', fontSize: '9px', color: '#777777'
        }).setOrigin(0.5, 0));

        var zone = this.add.zone(gateX, lineY + 70, gapH * 2 + pW * 2, pH + 20)
            .setInteractive({ useHandCursor: true });
        this.factoryContainer.add(zone);
        var self = this;
        zone.on('pointerup', function() {
            for (var j = 0; j < repos.length; j++) {
                if (repos[j][gateKey] === 0) {
                    var url = self.getGateLink(gateKey, repos[j].name, org);
                    if (url) window.open(url, '_blank');
                    return;
                }
            }
        });
    }

    getGateLink(gateKey, repoName, org) {
        if (gateKey === 'gha') return 'https://github.com/' + org + '/' + repoName + '/actions';
        if (gateKey === 'version_sync') return 'https://pypi.org/project/' + repoName + '/';
        if (gateKey === 'artifact_sync') return 'https://quay.io/repository/' + org + '/' + repoName;
        if (gateKey === 'constitution') return 'https://github.com/' + org + '/' + repoName + '/blob/main/.specify/memory/constitution.md';
        return null;
    }

    createPackage(repo, profileColor, startX, lineY, org) {
        var pkg = this.add.container(startX, lineY + 70);
        var box = this.add.rectangle(0, 0, 45, 30, profileColor, 0.25);
        box.setStrokeStyle(1.5, profileColor);
        pkg.add(box);
        pkg.setData('box', box);

        var sn = repo.name.replace(/^mcp-/, '').replace(/-crunchtools$/, '');
        if (sn.length > 8) sn = sn.substring(0, 7) + '\\u2026';
        pkg.add(this.add.text(0, -6, sn, {
            fontFamily: 'monospace', fontSize: '8px', color: '#e0e0e0', fontStyle: 'bold'
        }).setOrigin(0.5));

        if (repo.version && /^\\d/.test(repo.version)) {
            pkg.add(this.add.text(0, 5, 'v' + repo.version, {
                fontFamily: 'monospace', fontSize: '7px',
                color: repo.version_sync === 1 ? '#00ff88' : '#ff6666'
            }).setOrigin(0.5));
        }

        var xM = this.add.text(0, 0, 'X', {
            fontFamily: 'monospace', fontSize: '16px', color: '#ff4444', fontStyle: 'bold'
        }).setOrigin(0.5).setAlpha(0);
        pkg.add(xM);
        pkg.setData('xMark', xM);

        var ck = this.add.text(18, -12, '\\u2713', {
            fontFamily: 'sans-serif', fontSize: '10px', color: '#00ff88'
        }).setOrigin(0.5).setAlpha(0);
        pkg.add(ck);
        pkg.setData('check', ck);

        pkg.setSize(45, 30);
        pkg.setInteractive({ useHandCursor: true });

        var self = this;
        pkg.on('pointerover', function() {
            self.tweens.add({ targets: pkg, scaleX: 1.08, scaleY: 1.08, duration: 100 });
            self.showTooltip(pkg.x, pkg.y, repo);
        });
        pkg.on('pointerout', function() {
            self.tweens.add({ targets: pkg, scaleX: 1, scaleY: 1, duration: 100 });
            self.hideTooltip();
        });
        pkg.on('pointerup', function() {
            window.open('https://github.com/' + org + '/' + repo.name, '_blank');
        });

        this.factoryContainer.add(pkg);
        return pkg;
    }

    animatePackages() {
        var gen = this.animGeneration, self = this;
        for (var li = 0; li < this.productionLines.length; li++) {
            var line = this.productionLines[li];
            for (var pi = 0; pi < line.packages.length; pi++) {
                (function(pd, delay, ld) {
                    self.time.delayedCall(delay, function() {
                        if (self.animGeneration !== gen) return;
                        self.animatePackageThroughGate(
                            pd.container, pd.repo, 0,
                            ld.gateXPositions, pd.dockX, ld.lineY, gen, ld.org
                        );
                    });
                })(line.packages[pi], pi * 200, line);
            }
        }
    }

    animatePackageThroughGate(pkg, repo, gIdx, gateXPos, dockX, lineY, gen, org) {
        if (this.animGeneration !== gen) return;
        var gateKeys = ['gha', 'version_sync', 'artifact_sync', 'constitution'];

        if (gIdx >= gateKeys.length) {
            var self2 = this;
            this.tweens.add({
                targets: pkg, x: dockX, duration: 800, ease: 'Power1',
                onComplete: function() {
                    self2.showPackageShipped(pkg);
                    self2.playSound('chime');
                }
            });
            return;
        }

        var key = gateKeys[gIdx], val = repo[key], gateX = gateXPos[gIdx];
        if (val === null || val === undefined) {
            this.animatePackageThroughGate(pkg, repo, gIdx + 1, gateXPos, dockX, lineY, gen, org);
            return;
        }

        var self = this;
        this.tweens.add({
            targets: pkg, x: gateX, duration: 1200, ease: 'Power2',
            onComplete: function() {
                if (self.animGeneration !== gen) return;
                self.playSound('clunk');
                self.showGateScan(gateX, lineY, function() {
                    if (self.animGeneration !== gen) return;
                    if (val === 1) {
                        self.playSound('ding');
                        self.flashGate(gateX, lineY, true);
                        self.time.delayedCall(300, function() {
                            self.animatePackageThroughGate(
                                pkg, repo, gIdx + 1, gateXPos, dockX, lineY, gen, org
                            );
                        });
                    } else {
                        self.playSound('buzz');
                        self.flashGate(gateX, lineY, false);
                        self.showPackageFailed(pkg, gateX, lineY);
                    }
                });
            }
        });
    }

    showGateScan(gateX, lineY, cb) {
        var beam = this.add.rectangle(gateX, lineY + 35, 50, 2, 0x00d4ff, 0.6);
        this.factoryContainer.add(beam);
        this.tweens.add({
            targets: beam, y: lineY + 105, alpha: 0, duration: 400,
            onComplete: function() { beam.destroy(); if (cb) cb(); }
        });
    }

    flashGate(gateX, lineY, pass) {
        var c = pass ? 0x00ff88 : 0xff4444;
        var flash = this.add.rectangle(gateX, lineY + 70, 60, 80, c, 0.15);
        this.factoryContainer.add(flash);
        this.tweens.add({
            targets: flash, alpha: 0, duration: 300,
            onComplete: function() { flash.destroy(); }
        });
    }

    showPackageFailed(pkg, gateX, lineY) {
        var box = pkg.getData('box');
        if (box) box.setStrokeStyle(2, 0xff4444);
        var xM = pkg.getData('xMark');
        if (xM) this.tweens.add({ targets: xM, alpha: 1, duration: 200 });
        this.tweens.add({ targets: pkg, x: gateX - 5, duration: 150, ease: 'Back' });
        try {
            var burst = this.add.particles(gateX, lineY + 70, 'spark', {
                speed: { min: 30, max: 80 }, angle: { min: 0, max: 360 },
                scale: { start: 1, end: 0 }, alpha: { start: 1, end: 0 },
                tint: 0xff4444, lifespan: 500, quantity: 8, emitting: false
            });
            this.factoryContainer.add(burst);
            burst.explode(8);
            this.time.delayedCall(1000, function() { burst.destroy(); });
        } catch (e) { /* particles not supported */ }
    }

    showPackageShipped(pkg) {
        var box = pkg.getData('box');
        if (box) box.setStrokeStyle(1.5, 0x00ff88);
        var ck = pkg.getData('check');
        if (ck) this.tweens.add({ targets: ck, alpha: 1, duration: 200 });
        this.tweens.add({
            targets: pkg, alpha: { from: 1, to: 0.85 },
            duration: 1500, yoyo: true, repeat: -1, ease: 'Sine.easeInOut'
        });
    }

    update(time, delta) {
        for (var i = 0; i < this.conveyors.length; i++) {
            this.conveyors[i].tilePositionX -= delta * 0.09;
        }
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
