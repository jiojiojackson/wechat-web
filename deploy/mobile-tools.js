/* Mobile controls; uploads reuse the authenticated Selkies file channel. */
(() => {
    "use strict";
    const MAX_FILE = 100 * 1024 * 1024;
    const STORAGE_KEY = "wechat.mobile-tools.position.v1";
    function start() {
        if (document.getElementById("wechat-mobile-tools")) return;
        const icons = {
            tools: '<path d="M5 5h4v4H5zm10 0h4v4h-4zM5 15h4v4H5zm10 0h4v4h-4z"/>',
            keyboard: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M5 9h1m3 0h1m3 0h1m3 0h1M5 12h1m3 0h1m3 0h1m3 0h1M7 16h10"/>',
            photo: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
            file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8m-8 4h8"/>'
        };
        const svg = key => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[key]+'</svg>';
        const toolbar = document.createElement("div");
        toolbar.id = "wechat-mobile-tools";
        toolbar.className = "allow-native-input";
        toolbar.setAttribute("role", "group");
        toolbar.setAttribute("aria-label", "微信手机工具");
        toolbar.innerHTML = '<div id="wechat-tool-actions" hidden>'+
            '<button type="button" class="allow-native-input" data-tool="keyboard" aria-label="键盘">'+svg("keyboard")+'<span>键盘</span></button>'+
            '<button type="button" class="allow-native-input" data-tool="photo" aria-label="上传照片">'+svg("photo")+'<span>照片</span></button>'+
            '<button type="button" class="allow-native-input" data-tool="file" aria-label="上传文件">'+svg("file")+'<span>文件</span></button></div>'+
            '<button type="button" id="wechat-tool-toggle" class="allow-native-input" aria-label="展开工具，按住可拖动" aria-expanded="false" aria-controls="wechat-tool-actions">'+svg("tools")+'<span>工具</span></button>';
        document.body.appendChild(toolbar);
        const toggle = toolbar.querySelector("#wechat-tool-toggle");
        const actions = toolbar.querySelector("#wechat-tool-actions");
        const notice = document.createElement("div");
        notice.id = "wechat-upload-notice";
        notice.className = "allow-native-input";
        notice.hidden = true;
        notice.innerHTML = '<button type="button" class="allow-native-input" aria-label="关闭上传提示">×</button><p role="status" aria-live="polite"></p><progress max="100" value="0"></progress><small>保存位置：/config/Desktop（微信选择文件时打开“桌面”）</small>';
        document.body.appendChild(notice);
        const status = notice.querySelector("p");
        const progress = notice.querySelector("progress");
        notice.querySelector("button").addEventListener("click", () => { notice.hidden = true; });
        function inform(text, percent = null) {
            notice.hidden = false;
            status.textContent = text;
            progress.hidden = percent === null;
            if (percent !== null) progress.value = percent;
        }

        let position = {x: 1, y: 0.88};
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
            if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
                position = {x: Math.max(0, Math.min(1, saved.x)), y: Math.max(0, Math.min(1, saved.y))};
            }
        } catch (_) { /* Private mode can deny storage. */ }
        let expanded = false;
        let drag = null;
        let suppressClick = false;
        function bounds() {
            const viewport = window.visualViewport;
            return {left: viewport?.offsetLeft || 0, top: viewport?.offsetTop || 0,
                width: viewport?.width || innerWidth, height: viewport?.height || innerHeight};
        }
        function place() {
            const view = bounds();
            const rangeX = Math.max(0, view.width - 80);
            const rangeY = Math.max(0, view.height - 80);
            let x = view.left + 12 + position.x * rangeX;
            let y = view.top + 12 + position.y * rangeY;
            const compact = view.height < 292;
            const side = position.x > 0.5 ? "left" : "right";
            if (expanded) {
                if (compact) {
                    x = side === "left" ? Math.max(x, view.left + 204) : Math.min(x, view.left + view.width - 260);
                } else if (y - view.top >= view.height / 2) {
                    y = Math.max(y, view.top + 206);
                } else {
                    y = Math.min(y, view.top + view.height - 260);
                }
            }
            toolbar.style.left = x + "px";
            toolbar.style.top = y + "px";
            toolbar.dataset.direction = y - view.top >= 206 ? "up" : "down";
            // Short landscape viewports use a compact horizontal menu.
            toolbar.dataset.compact = compact ? "true" : "false";
            toolbar.dataset.side = side;
        }
        function expand(value) {
            expanded = value;
            actions.hidden = !expanded;
            toggle.setAttribute("aria-expanded", String(expanded));
            toggle.setAttribute("aria-label", (expanded ? "收起" : "展开") + "工具，按住可拖动");
            toggle.querySelector("span").textContent = expanded ? "收起" : "工具";
            place();
        }
        toggle.addEventListener("pointerdown", event => {
            if (!event.isPrimary || event.button !== 0) return;
            suppressClick = false;
            drag = {id: event.pointerId, x: event.clientX, y: event.clientY,
                startX: position.x, startY: position.y, moved: false};
            toggle.setPointerCapture(event.pointerId);
        });
        toggle.addEventListener("pointermove", event => {
            if (!drag || drag.id !== event.pointerId) return;
            const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
            if (Math.hypot(dx, dy) > 7) drag.moved = true;
            if (!drag.moved) return;
            const view = bounds();
            position = {x: Math.max(0, Math.min(1, drag.startX + dx / Math.max(1, view.width - 80))),
                y: Math.max(0, Math.min(1, drag.startY + dy / Math.max(1, view.height - 80)))};
            place();
        });
        function finishDrag(event) {
            if (!drag || drag.id !== event.pointerId) return;
            suppressClick = drag.moved || event.type === "pointercancel";
            if (toggle.hasPointerCapture(event.pointerId)) toggle.releasePointerCapture(event.pointerId);
            drag = null;
            try { localStorage.setItem(STORAGE_KEY, JSON.stringify(position)); } catch (_) { }
        }
        toggle.addEventListener("pointerup", finishDrag);
        toggle.addEventListener("pointercancel", finishDrag);
        toggle.addEventListener("click", () => {
            if (suppressClick) { suppressClick = false; return; }
            expand(!expanded);
        });
        window.addEventListener("resize", place);
        window.visualViewport?.addEventListener("resize", place);
        window.visualViewport?.addEventListener("scroll", place);
        place();
        toolbar.querySelector('[data-tool="keyboard"]').addEventListener("click", () => {
            const helper = document.getElementById("keyboard-input-assist");
            if (!helper) { inform("桌面仍在连接，请稍后再打开键盘。"); return; }
            expand(false);
            helper.removeAttribute("aria-hidden");
            helper.focus({preventScroll: true});
            const overlay = document.getElementById("overlayInput");
            overlay?.addEventListener("touchstart", () => {
                if (document.activeElement === helper) helper.blur();
            }, {once: true, passive: true});
        });

        let batch = null;
        let current = null;
        let externalUpload = false;
        let watchdog = null;
        let needsReload = false;
        const uploadButtons = [...toolbar.querySelectorAll('[data-tool="photo"], [data-tool="file"]')];
        function resetWatchdog() {
            clearTimeout(watchdog);
            watchdog = setTimeout(() => {
                needsReload = true;
                fail("上传连接超时，请刷新页面后重试；未确认的文件可能不完整。");
            }, 120000);
        }
        function fail(message) {
            clearTimeout(watchdog);
            if (current) current.failed = true;
            batch = null;
            current = null;
            for (const button of uploadButtons) button.disabled = needsReload;
            inform(message);
        }
        function safeName(name) {
            let clean = name.normalize("NFC").replace(/[\x00-\x1f\x7f/\\:<>"|?*]/g, "_").replace(/^\.+/, "");
            if (!clean) clean = "未命名文件";
            // Keep the extension within Linux's 255-byte component limit.
            const dot = clean.lastIndexOf(".");
            const extension = dot > 0 ? clean.slice(dot, dot + 16) : "";
            let stem = dot > 0 ? clean.slice(0, dot) : clean;
            while (new TextEncoder().encode(stem + extension).length > 170) stem = Array.from(stem).slice(0, -1).join("");
            return stem + extension;
        }
        const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
        async function confirmSaved(item) {
            const url = "/files/" + item.path.split("/").map(encodeURIComponent).join("/");
            for (let attempt = 0; attempt < 40 && !item.failed; attempt++) {
                try {
                    const response = await fetch(url, {method: "HEAD", cache: "no-store", credentials: "same-origin",
                        redirect: "error", signal: AbortSignal.timeout(4000)});
                    if (response.ok && Number(response.headers.get("Content-Length")) === item.file.size) return true;
                    if ([401, 403].includes(response.status)) return false;
                } catch (_) { /* Retry while the existing channel drains to disk. */ }
                await pause(500);
            }
            return false;
        }
        function next() {
            if (!batch) return;
            if (batch.index >= batch.files.length) {
                const count = batch.files.length;
                clearTimeout(watchdog);
                batch = null;
                current = null;
                for (const button of uploadButtons) button.disabled = false;
                inform("已上传 " + count + " 项到：/config/Desktop（桌面）", 100);
                return;
            }
            const bridge = document.getElementById("globalFileInput");
            if (!bridge || bridge.disabled) { fail("上传功能尚未就绪，请等待桌面连接后重试。"); return; }
            const file = batch.files[batch.index];
            const random = Array.from(crypto.getRandomValues(new Uint8Array(4)), x => x.toString(16).padStart(2, "0")).join("");
            const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "");
            const path = stamp + "-" + random + "_" + safeName(file.name);
            current = {file, path, failed: false, confirming: false};
            inform("正在上传 " + (batch.index + 1) + "/" + batch.files.length + "：" + file.name, 0);
            try {
                const transfer = new DataTransfer();
                transfer.items.add(new File([file], path, {type: file.type, lastModified: file.lastModified}));
                bridge.files = transfer.files;
                resetWatchdog();
                bridge.dispatchEvent(new Event("change", {bubbles: true}));
            } catch (_) { fail("当前浏览器无法启动上传，请更新 Safari 后重试。"); }
        }
        window.addEventListener("message", async event => {
            if (event.source !== window || event.origin !== location.origin || event.data?.type !== "fileUpload") return;
            const payload = event.data.payload;
            if (!payload || typeof payload !== "object") return;
            if (!batch || !current) {
                if (payload.status === "start") externalUpload = true;
                if (["end", "error"].includes(payload.status)) externalUpload = false;
                return;
            }
            const item = current;
            if (["error", "warning", "warn"].includes(payload.status)) {
                fail("上传失败，请检查连接和容器剩余空间后重试；已完成的文件会保留。"); return;
            }
            if (payload.fileName !== item.path) return;
            resetWatchdog();
            if (payload.status === "progress") {
                const percent = Math.max(0, Math.min(100, Number(payload.progress) || 0));
                inform("正在上传 " + (batch.index + 1) + "/" + batch.files.length + "：" + item.file.name + "（" + percent + "%）", percent);
            }
            if (payload.status === "end" && !item.confirming) {
                item.confirming = true;
                inform("正在确认文件已保存：" + item.file.name, 100);
                if (!await confirmSaved(item)) {
                    if (current === item) fail("尚未确认文件保存成功，请检查连接和容器剩余空间后重试。");
                    return;
                }
                if (current !== item || !batch) return;
                batch.index++;
                // Let the upstream change handler finish resetting its input/channel.
                setTimeout(next, 50);
            }
        });
        for (const kind of ["photo", "file"]) {
            const picker = document.createElement("input");
            picker.type = "file";
            picker.multiple = true;
            picker.hidden = true;
            picker.id = "wechat-" + kind + "-picker";
            picker.className = "allow-native-input";
            if (kind === "photo") picker.accept = "image/*";
            document.body.appendChild(picker);
            toolbar.querySelector('[data-tool="' + kind + '"]').addEventListener("click", () => {
                if (needsReload) { inform("请刷新页面后重试上传。"); return; }
                if (batch || externalUpload) { inform("请等待当前上传完成。"); return; }
                picker.value = "";
                picker.click(); // Synchronous user gesture is required by iOS.
            });
            picker.addEventListener("change", () => {
                const files = Array.from(picker.files || []);
                picker.value = "";
                if (!files.length) return;
                if (needsReload) { inform("请刷新页面后重试上传。"); return; }
                if (batch || externalUpload) { inform("请等待当前上传完成。"); return; }
                if (files.length > 20 || files.reduce((sum, f) => sum + f.size, 0) > 500 * 1024 * 1024) {
                    inform("每次最多选择 20 项、合计 500 MiB，请分批上传。"); return;
                }
                if (files.some(file => file.size > MAX_FILE)) { inform("单个文件请控制在 100 MiB 以内。"); return; }
                batch = {files, index: 0};
                for (const button of uploadButtons) button.disabled = true;
                expand(false);
                next();
            });
        }
        // Inputs retain the existing IME handling; these controls do not emit remote clicks.
        for (const node of [toolbar, notice]) {
            for (const type of ["pointerdown", "pointerup", "pointermove", "touchstart", "touchend", "touchmove", "mousedown", "mouseup", "click"]) {
                node.addEventListener(type, event => event.stopPropagation());
            }
        }
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
    else start();
})();
