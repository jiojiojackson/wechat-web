/* Use phone-sized logical pixels, rather than the phone's 2x/3x backing pixels. */
(() => {
    "use strict";
    const phone = /iPhone|iPod|Android.*Mobile/i.test(navigator.userAgent) ||
        (matchMedia("(pointer: coarse)").matches && Math.min(screen.width, screen.height) <= 600);
    // Shared/secondary views do not control the primary desktop's resolution.
    if (location.hash.startsWith("#shared") || location.hash.startsWith("#player") || location.hash.startsWith("#display2")) return;
    const prefix = location.href.split("#")[0].replace(/[^a-zA-Z0-9.-_]/g, "_") + "_";
    // A shared native application cannot reliably reflow between a Retina
    // desktop's 192 DPI and a phone's 96 DPI. Use logical pixels on both;
    // only the desktop resolution changes when the active device changes.
    try {
        for (const [name, value] of Object.entries({useCssScaling: true, use_css_scaling: true, scaling_dpi: 96})) {
            localStorage.setItem(prefix + name, String(value));
        }
    } catch (_) { /* The server also locks these common display settings. */ }
    if (!phone) return;
    const even = value => Math.ceil(value / 2) * 2;
    let base = {width: innerWidth, height: window.visualViewport?.height || innerHeight};
    let target;
    let lastSent = "";
    let timer;
    let connected = false;
    let firstFrameCheckScheduled = false;
    let restorePending = false;

    function resolution() {
        // Keep all native WeChat dialogs within their published minimum window size.
        const factor = Math.max(1, 480 / base.width, 560 / base.height);
        return {width: even(Math.min(2048, base.width * factor)),
            height: even(Math.min(2048, base.height * factor))};
    }
    function seed() {
        target = resolution();
        const settings = {useCssScaling: true, use_css_scaling: true, scaleLocallyManual: true,
            is_manual_resolution_mode: true, manual_width: target.width,
            manual_height: target.height, scaling_dpi: 96};
        try {
            for (const [name, value] of Object.entries(settings)) localStorage.setItem(prefix + name, String(value));
        } catch (_) { /* Runtime messages still work when storage is restricted. */ }
    }
    seed(); // Must run before the upstream module reads its initial settings.
    // The initial SETTINGS already contain this size. Re-applying it on the
    // connection/status events closes a decoder while its first frame configures.
    lastSent = target.width + "x" + target.height;
    document.documentElement.classList.add("wechat-phone-view");
    const css = document.createElement("style");
    css.textContent = `html.wechat-phone-view #keyboard-input-assist { font-size: 16px !important; }
    html.wechat-phone-view #videoCanvas, html.wechat-phone-view #overlayInput {
        position: absolute !important;
        width: var(--wechat-phone-width) !important; height: var(--wechat-phone-height) !important;
        left: var(--wechat-phone-left) !important; top: var(--wechat-phone-top) !important;
        object-fit: fill !important;
    }`;
    document.head.appendChild(css);
    const post = message => window.postMessage(message, location.origin);
    function applyResolution() {
        if (!connected || document.hidden) return;
        const signature = target.width + "x" + target.height;
        const canvas = document.getElementById("videoCanvas");
        const changedByPeer = canvas && (canvas.width !== target.width || canvas.height !== target.height);
        if (signature === lastSent && !changedByPeer && !restorePending) return;
        lastSent = signature;
        restorePending = false;
        post({type: "setManualResolution", width: target.width, height: target.height});
    }
    function layout() {
        if (document.hidden) return;
        const viewport = window.visualViewport;
        if (viewport && Math.abs(viewport.scale - 1) > 0.05) return;
        const width = innerWidth;
        const height = viewport?.height || innerHeight;
        const active = document.activeElement;
        const editable = active && (active.id === "keyboard-input-assist" ||
            (active.matches("input:not([type=file]), textarea, [contenteditable=true]") && active !== document.getElementById("overlayInput")));
        const rotated = Math.abs(width - base.width) > 48;
        const keyboard = !rotated && editable && base.height - height > 120;
        if (!keyboard) {
            base = {width, height: rotated && editable ? Math.max(innerHeight, height) : height};
            seed();
        }
        const container = document.querySelector(".video-container");
        const rect = container?.getBoundingClientRect() || {left: 0, top: 0};
        const scale = Math.min(base.width / target.width, base.height / target.height);
        const sceneWidth = target.width * scale;
        const sceneHeight = target.height * scale;
        const top = keyboard ? (viewport?.offsetTop || 0) + height - sceneHeight - rect.top :
            (base.height - sceneHeight) / 2 - rect.top;
        const style = document.documentElement.style;
        style.setProperty("--wechat-phone-width", sceneWidth + "px");
        style.setProperty("--wechat-phone-height", sceneHeight + "px");
        style.setProperty("--wechat-phone-left", ((width - sceneWidth) / 2 - rect.left) + "px");
        style.setProperty("--wechat-phone-top", top + "px");
        applyResolution();
    }
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(layout, 160);
    }
    window.addEventListener("resize", schedule);
    window.addEventListener("orientationchange", schedule);
    window.visualViewport?.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("scroll", schedule);
    window.addEventListener("focusin", schedule);
    window.addEventListener("focusout", schedule);
    // Returning to an existing Safari tab may not fire resize at all, even
    // though another device has changed the single shared remote desktop.
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) restorePending = true;
        else schedule();
    });
    window.addEventListener("pagehide", () => { restorePending = true; });
    window.addEventListener("pageshow", event => {
        if (event.persisted) restorePending = true;
        schedule();
    });
    window.addEventListener("focus", schedule);
    window.addEventListener("message", event => {
        if (event.source !== window || event.origin !== location.origin) return;
        if (event.data?.type === "serverSettings") schedule();
        if (event.data?.type === "pipelineStatusUpdate" && event.data.video === false) connected = false;
        if (event.data?.type === "pipelineStatusUpdate" && event.data.video === true) {
            connected = true;
            // Runs after upstream finishes attaching its real coordinate mapper.
            schedule();
            if (!firstFrameCheckScheduled) {
                firstFrameCheckScheduled = true;
                setTimeout(() => {
                    const status = document.getElementById("status-display");
                    if (!status || status.classList.contains("hidden") ||
                        !/waiting for stream/i.test(status.textContent)) return;
                    // A static desktop may not generate another damaged frame if
                    // the initial frame arrived before its decoder was ready.
                    // Restart only video once; retain the desktop/login/socket.
                    post({type: "pipelineControl", pipeline: "video", enabled: false});
                    setTimeout(() => post({type: "pipelineControl", pipeline: "video", enabled: true}), 350);
                }, 3500);
            }
        }
    });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", layout, {once: true});
    else layout();
})();
