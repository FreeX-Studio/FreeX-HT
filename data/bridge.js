/*
 * Copyright (c) 2026 MirraNET, Niklas Linz. All rights reserved.
 *
 * This file is part of the MirraNET project and is licensed under the
 * GNU Lesser General Public License v3.0 (LGPLv3).
 *
 * You may use, distribute and modify this code under the terms
 * of the LGPLv3 license. You should have received a copy of the
 * license along with this file. If not, see <https://www.gnu.org/licenses/lgpl-3.0.html>
 * or contact: niklas.linz@mirranet.de
 */

/*
 * FreeXHT bridge: window.FreeXNative for the FreeX TV interface, like the one of the Android app,
 * so the interface drives mpv as it drives ExoPlayer there (backends.js, NativeBackend).
 *
 * Served by freexht-agent as /freexht/bridge.js and loaded before backends.js. Commands go to the
 * agent over a WebSocket; mpv's events come back to window.FreeXNativeEvent. What the page asks
 * for synchronously (capabilities, device name) comes with this file.
 */
(function () {
    "use strict";

    const INFO = __FREEXHT_INFO__;

    let ws = null;
    let queue = [];
    let seq = 0;
    const pending = {};

    function connect() {
        ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/freexht/ws");
        ws.onopen = () => {
            const q = queue;
            queue = [];
            q.forEach((m) => ws.send(m));
        };
        ws.onmessage = (e) => {
            let m;
            try {
                m = JSON.parse(e.data);
            } catch (err) {
                return;
            }
            if (m.event) {
                if (typeof window.FreeXNativeEvent === "function") window.FreeXNativeEvent(m.event);
                return;
            }
            const p = pending[m.id];
            if (!p) return;
            delete pending[m.id];
            clearTimeout(p.timer);
            if (m.error !== undefined) p.reject(new Error(m.error));
            else p.resolve(m.result);
        };
        ws.onclose = () => {
            ws = null;
            Object.keys(pending).forEach((id) => {
                clearTimeout(pending[id].timer);
                pending[id].reject(new Error("FreeXHT agent not reachable"));
                delete pending[id];
            });
            setTimeout(connect, 1000);
        };
    }

    function send(obj) {
        const s = JSON.stringify(obj);
        if (ws && ws.readyState === 1) ws.send(s);
        else queue.push(s);
    }

    /** A call to the agent (system functions); resolves with its result. */
    function call(method, args, timeoutMs) {
        return new Promise((resolve, reject) => {
            const id = ++seq;
            const timer = setTimeout(() => {
                if (!pending[id]) return;
                delete pending[id];
                reject(new Error("timeout"));
            }, timeoutMs || 90000);
            pending[id] = {resolve, reject, timer};
            send({id, call: method, args: args || {}});
        });
    }

    window.FreeXNative = {
        platform: "freexht",
        // The player (the same calls as the Android app's bridge).
        open: (json) => send({cmd: "open", args: JSON.parse(json)}),
        play: () => send({cmd: "play"}),
        pause: () => send({cmd: "pause"}),
        seek: (ms) => send({cmd: "seek", args: {ms: Number(ms) || 0}}),
        stop: () => send({cmd: "stop"}),
        exit: () => send({cmd: "exit"}),
        deviceName: () => INFO.deviceName,
        capabilities: () => JSON.stringify(INFO.caps),
        // FreeXHT only.
        info: () => INFO,
        defaultServer: () => INFO.server || "",
        call,
    };

    connect();
})();
